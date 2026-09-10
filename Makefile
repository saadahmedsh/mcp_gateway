PYTHON ?= .venv/bin/python
COMPOSE ?= docker compose

.PHONY: up down test lint demo eval install sandbox-build

install:
	$(PYTHON) -m pip install --requirement requirements.lock

sandbox-build:
	docker build --tag mcp-gateway-tool:local --file docker/Dockerfile.tool-base .

up: sandbox-build
	$(COMPOSE) up --detach --wait

down:
	$(COMPOSE) down

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m black --check .
	$(PYTHON) -m mypy gateway eval tests

demo:
	curl --fail --silent --show-error http://localhost:8181/health
	$(COMPOSE) exec --no-TTY redis redis-cli ping
	curl --fail --silent --show-error --output /dev/null http://localhost:16686/
	GATEWAY_APPROVAL_TIMEOUT_SECONDS=1 $(PYTHON) -m eval.client

eval:
	$(PYTHON) -m pytest tests
