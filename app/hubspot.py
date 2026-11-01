"""HubSpot CRM client: Private App token, paged reads, email search, scope probe."""
from __future__ import annotations

from typing import Iterator

from .config import CONTACT_PROPERTIES, HUBSPOT_BASE
from .http import RetryExhausted, Transport


class HubSpotError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class HubSpot:
    def __init__(self, token: str, transport: Transport, base: str = HUBSPOT_BASE):
        self.token = token
        self.transport = transport
        self.base = base.rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    # ------------------------------------------------------------------ reads
    def iter_contacts(
        self,
        properties: list[str] | None = None,
        page_size: int = 100,
        max_pages: int = 50,
    ) -> Iterator[dict]:
        properties = properties or CONTACT_PROPERTIES
        after: str | None = None
        for _ in range(max_pages):
            params = {"limit": page_size, "properties": ",".join(properties), "archived": "false"}
            if after:
                params["after"] = after
            response = self.transport.request(
                "GET", f"{self.base}/crm/v3/objects/contacts", headers=self._headers(), params=params
            )
            if not response.ok:
                raise HubSpotError(f"contact list failed: {response.status}", response.status)
            body = response.body or {}
            for contact in body.get("results", []):
                yield contact
            after = (body.get("paging") or {}).get("next", {}).get("after")
            if not after:
                return

    def get_contact(self, contact_id: str, properties: list[str] | None = None) -> dict:
        properties = properties or CONTACT_PROPERTIES
        response = self.transport.request(
            "GET",
            f"{self.base}/crm/v3/objects/contacts/{contact_id}",
            headers=self._headers(),
            params={"properties": ",".join(properties)},
        )
        if response.status == 404:
            raise HubSpotError(f"contact {contact_id} not found", 404)
        if not response.ok:
            raise HubSpotError(f"contact read failed: {response.status}", response.status)
        return response.body or {}

    def search_by_email(self, email: str, properties: list[str] | None = None) -> list[dict]:
        properties = properties or CONTACT_PROPERTIES
        payload = {
            "filterGroups": [
                {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
            ],
            "properties": properties,
            "limit": 10,
        }
        response = self.transport.request(
            "POST", f"{self.base}/crm/v3/objects/contacts/search", headers=self._headers(), json=payload
        )
        if not response.ok:
            raise HubSpotError(f"contact search failed: {response.status}", response.status)
        return (response.body or {}).get("results", [])

    # ------------------------------------------------------------------ probe
    def probe(self) -> list[dict]:
        """Call one read endpoint per scope so the run records what the token can do."""
        checks = [
            ("crm.objects.contacts.read", "GET", "/crm/v3/objects/contacts?limit=1"),
            ("crm.objects.companies.read", "GET", "/crm/v3/objects/companies?limit=1"),
            ("crm.objects.deals.read", "GET", "/crm/v3/objects/deals?limit=1"),
            ("crm.schemas.contacts.read", "GET", "/crm/v3/properties/contacts"),
            ("crm.objects.owners.read", "GET", "/crm/v3/owners?limit=1"),
        ]
        results = []
        for scope, method, path in checks:
            try:
                response = self.transport.request(method, f"{self.base}{path}", headers=self._headers())
                results.append({"scope": scope, "status": response.status, "allowed": response.ok})
            except RetryExhausted as exc:
                results.append({"scope": scope, "status": exc.status, "allowed": False})
        return results
