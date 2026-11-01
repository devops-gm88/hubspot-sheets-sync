"""HTTP transport with bounded retries, backoff and idempotency keys.

The transport is injected everywhere, so tests exercise the retry rules without a network.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from .config import BASE_DELAY, MAX_ATTEMPTS

RETRY_STATUS = {429, 500, 502, 503, 504}


class RetryExhausted(RuntimeError):
    """All attempts failed. Carries the last status and body for the run log."""

    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class Response:
    status: int
    body: Any
    headers: dict = field(default_factory=dict)
    attempts: int = 1
    text: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Transport:
    def __init__(
        self,
        session: requests.Session | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        base_delay: float = BASE_DELAY,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ):
        self.session = session or requests.Session()
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.sleep = sleep
        self.jitter = jitter

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        json: Any = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
        timeout: int = 30,
    ) -> Response:
        headers = dict(headers or {})
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                raw = self.session.request(
                    method, url, headers=headers, json=json, params=params, timeout=timeout
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                self._backoff(attempt)
                continue

            try:
                body = raw.json()
            except ValueError:
                body = None

            if raw.status_code in RETRY_STATUS and attempt < self.max_attempts:
                retry_after = raw.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    self.sleep(min(float(retry_after), 30.0))
                else:
                    self._backoff(attempt)
                continue

            if raw.status_code in RETRY_STATUS:
                raise RetryExhausted(
                    f"{method} {url} failed with {raw.status_code} after {attempt} attempts",
                    status=raw.status_code,
                    body=raw.text[:500],
                )

            return Response(
                status=raw.status_code,
                body=body,
                headers=dict(raw.headers),
                attempts=attempt,
                text=raw.text[:500],
            )

        raise RetryExhausted(f"{method} {url} failed: {last_error}")

    def _backoff(self, attempt: int) -> None:
        delay = self.base_delay * (2 ** (attempt - 1))
        delay = delay * (0.5 + self.jitter() / 2)
        self.sleep(delay)
