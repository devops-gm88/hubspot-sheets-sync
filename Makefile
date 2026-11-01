PY      ?= .venv/bin/python
PIP     ?= .venv/bin/pip
PYTEST  ?= .venv/bin/pytest

.DEFAULT_GOAL := help

help:
	@echo "make install  create .venv and install dependencies"
	@echo "make probe    show which HubSpot scopes the token really has"
	@echo "make sync     pull every contact and upsert the sheet"
	@echo "make serve    run the webhook receiver on port 8090"
	@echo "make test     run the offline test suite"
	@echo "make sign     print a valid webhook signature (BODY=... TS=...)"

install:
	python3 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -r requirements-dev.txt

probe:
	$(PY) -m app.cli probe

sync:
	$(PY) -m app.cli sync

serve:
	$(PY) -m app.cli serve --port 8090

test:
	$(PYTEST)

sign:
	$(PY) -m app.cli sign --body "$(BODY)" --timestamp "$(TS)"

.PHONY: help install probe sync serve test sign
