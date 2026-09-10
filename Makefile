PYTHON ?= .venv/bin/python
COMPOSE ?= docker compose

.PHONY: up down test lint demo eval eval-live eval-realistic install sandbox-build kind-up kind-load kind-deploy kind-down

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
	$(PYTHON) -m eval.harness
	$(PYTHON) -m eval.report

eval-live:
	$(PYTHON) -m eval.live

eval-realistic:
	$(PYTHON) -m eval.realistic

kind-up:
	@if kind get clusters | grep -qx 'mcp-gateway'; then \
		echo 'Kind cluster mcp-gateway already exists'; \
	else \
		kind create cluster --config docker/kind-config.yaml; \
	fi

kind-load: kind-up
	docker build --tag mcp-gateway:kind --file docker/Dockerfile.gateway .
	kind load docker-image mcp-gateway:kind --name mcp-gateway

kind-deploy: kind-load
	kubectl apply --filename docker/kind-dependencies.yaml
	kubectl rollout status deployment/redis --timeout=120s
	kubectl rollout status deployment/opa --timeout=120s
	helm upgrade --install mcp-gateway deploy/helm/mcp-gateway \
		--set image.repository=mcp-gateway \
		--set image.tag=kind \
		--set image.pullPolicy=Never \
		--set service.type=NodePort

kind-down:
	kind delete cluster --name mcp-gateway
