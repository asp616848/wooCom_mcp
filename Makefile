# One-command local setup. Run `make help` for targets.

CLUSTER     ?= woo-mcp
STORE       ?= main
STORE_NS    ?= store-$(STORE)
STORE_CHART := charts/woo-store
ENV         ?= local

UV_RUN      := uv run --all-packages --locked
PY_SRC       = $(filter-out services/dashboard/%,$(wildcard packages/*/src/* services/*/src/*))
CHARTS       = $(wildcard charts/*)
DASHBOARD   := services/dashboard
TAG_FILE    := .image-tag
# every services/<name>/Dockerfile becomes image wmg/<name>; override to build a subset: make images SERVICES=woo-mcp
SERVICES    ?= $(patsubst services/%/Dockerfile,%,$(wildcard services/*/Dockerfile))
IMAGE_TAG    = $(shell cat $(TAG_FILE) 2>/dev/null)
# charts refuse to render without image tags; lint/kubeconform use a placeholder
CHART_CHECK_SET := --set-string wooMcp.image.tag=check --set-string image.repository=check --set-string image.tag=check \
                   --set-string gateway.image.tag=check --set-string dashboard.image.tag=check
UPSTREAMS_NS := mcp-upstreams
SERVER_CHART := charts/mcp-server
PLATFORM_NS  := mcp-platform
PLATFORM_CHART := charts/mcp-platform
# seed slug  <-  namespace/secret/key holding that upstream's credential
SEED_SOURCES := woo-main=$(STORE_NS)/$(STORE)-store-credentials/mcp-token \
                gst=$(UPSTREAMS_NS)/gst-mcp-auth/token \
                pincode=$(UPSTREAMS_NS)/pincode-mcp-auth/token

.PHONY: help up down reset cluster cluster-delete store store-uninstall store-test store-admin-password setup-logs \
        status deps lint chart-lint typecheck mypy test test-integration images kubeconform upstreams upstreams-uninstall \
        platform-ns seed-credentials secrets-llm platform platform-uninstall admin-password

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-20s %s\n", $$1, $$2}'

up: cluster images upstreams store seed-credentials platform ## Create cluster (if missing), build images, install everything

down: platform-uninstall store-uninstall upstreams-uninstall ## Uninstall releases (keeps cluster, data and Secrets)

reset: cluster-delete up ## Wipe the cluster and all data, then rebuild

cluster: ## Create the local k3d cluster if it does not exist
	@k3d cluster list $(CLUSTER) >/dev/null 2>&1 || \
	  k3d cluster create $(CLUSTER) --servers 1 --agents 0 \
	    --port "80:80@loadbalancer" --port "443:443@loadbalancer" --wait
	@kubectl config use-context k3d-$(CLUSTER)

cluster-delete: ## Delete the k3d cluster (destroys all data)
	k3d cluster delete $(CLUSTER)

store: ## Install/upgrade the store (STORE=main ENV=local; woo-mcp image tag from .image-tag, run `make images` first)
	@test -n "$(IMAGE_TAG)" || { echo "No $(TAG_FILE) - run 'make images' first"; exit 1; }
	helm upgrade --install $(STORE) $(STORE_CHART) -n $(STORE_NS) --create-namespace \
	  -f $(STORE_CHART)/values-$(ENV).yaml --set-string wooMcp.image.tag=$(IMAGE_TAG) --wait --timeout 10m

upstreams: ## Install/upgrade gst (bearer) + pincode (X-Api-Key) in mcp-upstreams (image tag from .image-tag)
	@test -n "$(IMAGE_TAG)" || { echo "No $(TAG_FILE) - run 'make images' first"; exit 1; }
	kubectl create namespace $(UPSTREAMS_NS) --dry-run=client -o yaml | kubectl apply -f -
	kubectl label --overwrite namespace $(UPSTREAMS_NS) wmg.dev/role=upstreams pod-security.kubernetes.io/enforce=restricted
	helm upgrade --install gst $(SERVER_CHART) -n $(UPSTREAMS_NS) -f $(SERVER_CHART)/values-$(ENV).yaml \
	  --set image.repository=wmg/mcp-gst --set-string image.tag=$(IMAGE_TAG) --set auth.type=bearer --set size=small \
	  --set networkPolicy.allowInternet=false --wait --timeout 3m
	helm upgrade --install pincode $(SERVER_CHART) -n $(UPSTREAMS_NS) -f $(SERVER_CHART)/values-$(ENV).yaml \
	  --set image.repository=wmg/mcp-pincode --set-string image.tag=$(IMAGE_TAG) --set auth.type=header \
	  --set auth.headerName=X-Api-Key --set size=small --set networkPolicy.allowInternet=false --wait --timeout 3m

upstreams-uninstall: ## Uninstall gst + pincode (credential Secrets are kept)
	-helm uninstall gst -n $(UPSTREAMS_NS)
	-helm uninstall pincode -n $(UPSTREAMS_NS)

platform-ns:
	@kubectl create namespace $(PLATFORM_NS) --dry-run=client -o yaml | kubectl apply -f - >/dev/null
	@kubectl label --overwrite namespace $(PLATFORM_NS) wmg.dev/role=platform pod-security.kubernetes.io/enforce=restricted >/dev/null

seed-credentials: platform-ns ## Copy upstream credentials into mcp-platform Secrets <slug>-upstream-credential (values never printed)
	@for pair in $(SEED_SOURCES); do \
	  slug=$${pair%%=*}; src=$${pair#*=}; ns=$${src%%/*}; rest=$${src#*/}; secret=$${rest%%/*}; key=$${rest#*/}; \
	  if ! kubectl get secret $$secret -n $$ns >/dev/null 2>&1; then echo "skip $$slug: $$ns/$$secret not found"; continue; fi; \
	  kubectl get secret $$secret -n $$ns -o jsonpath="{.data.$$key}" | base64 -d | \
	    kubectl create secret generic $$slug-upstream-credential -n $(PLATFORM_NS) --from-file=credential=/dev/stdin \
	      --dry-run=client -o yaml | kubectl apply -f - >/dev/null && echo "seeded $$slug-upstream-credential"; \
	done

secrets-llm: platform-ns ## Create Secret platform-llm from GEMINI_API_KEY in .env (skips with a warning if missing)
	@key=$$(grep -E '^GEMINI_API_KEY=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r'); \
	if [ -z "$$key" ]; then echo "WARNING: GEMINI_API_KEY not set in .env - chat will be unavailable"; exit 0; fi; \
	printf '%s' "$$key" | kubectl create secret generic platform-llm -n $(PLATFORM_NS) --from-file=api-key=/dev/stdin \
	  --dry-run=client -o yaml | kubectl apply -f - >/dev/null && echo "platform-llm Secret updated"

platform: platform-ns ## Install/upgrade gateway + dashboard + Postgres (image tags from .image-tag)
	@test -n "$(IMAGE_TAG)" || { echo "No $(TAG_FILE) - run 'make images' first"; exit 1; }
	helm upgrade --install platform $(PLATFORM_CHART) -n $(PLATFORM_NS) -f $(PLATFORM_CHART)/values-$(ENV).yaml \
	  --set-string gateway.image.tag=$(IMAGE_TAG) --set-string dashboard.image.tag=$(IMAGE_TAG) --wait --timeout 5m

platform-uninstall: ## Uninstall the platform release (Postgres PVC and Secrets are kept)
	-helm uninstall platform -n $(PLATFORM_NS)

admin-password: ## Print the dashboard bootstrap admin password (user: admin@example.com)
	@kubectl get secret platform-credentials -n $(PLATFORM_NS) -o jsonpath='{.data.admin-password}' | base64 -d; echo

store-uninstall: ## Uninstall the store release (PVCs and Secret are kept)
	-helm uninstall $(STORE) -n $(STORE_NS)

store-test: ## Run the store's helm tests (selfcheck: signed REST calls + get_store_info via woo-mcp)
	helm test $(STORE) -n $(STORE_NS) --logs --timeout 3m

store-admin-password: ## Print the WordPress admin password (user: admin)
	@kubectl get secret $(STORE)-store-credentials -n $(STORE_NS) -o jsonpath='{.data.wordpress-admin-password}' | base64 -d; echo

setup-logs: ## Show logs of the last store setup Job
	kubectl logs -n $(STORE_NS) job/$(STORE)-setup --tail=200

status: ## Show store, upstream and platform resources
	kubectl get all,pvc,ingress -n $(STORE_NS)
	-kubectl get all,networkpolicy -n $(UPSTREAMS_NS)
	-kubectl get all,pvc,ingress -n $(PLATFORM_NS)

deps: ## Install the Python workspace (and dashboard deps once it exists)
	uv sync --all-packages --locked
	@if [ -f $(DASHBOARD)/package.json ]; then cd $(DASHBOARD) && npm ci; fi

lint: chart-lint ## ruff (lint + format check), dashboard ESLint, helm lint (every chart x local/prod)
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .
	@if [ -f $(DASHBOARD)/package.json ]; then cd $(DASHBOARD) && npm run lint; fi

chart-lint: ## helm lint every chart with local and prod values
	@for c in $(CHARTS); do for e in local prod; do \
	  if [ -f $$c/values-$$e.yaml ]; then helm lint $$c -f $$c/values-$$e.yaml $(CHART_CHECK_SET) --quiet || exit 1; fi; \
	done; done

typecheck: mypy ## mypy --strict (Python) and tsc (dashboard)
	@if [ -f $(DASHBOARD)/package.json ]; then cd $(DASHBOARD) && npm run typecheck; fi

mypy: ## mypy --strict on every Python package (also used by CI)
	$(UV_RUN) mypy $(PY_SRC)

test: ## Unit tests: pytest (and vitest once the dashboard exists)
	$(UV_RUN) pytest -m "not integration"
	@if [ -f $(DASHBOARD)/package.json ]; then cd $(DASHBOARD) && npm test; fi

test-integration: ## Integration tests (needs Docker: starts postgres:17.11-alpine3.24 on localhost:55432)
	$(UV_RUN) pytest -m integration

images: ## Build service images (tag dev-<sha>[-<ts> if dirty] -> .image-tag) and import them into k3d
	@if [ -z "$(strip $(SERVICES))" ]; then echo "No services/*/Dockerfile yet - nothing to build"; exit 0; fi; \
	 sha=$$(git rev-parse --short HEAD); \
	 if [ -n "$$(git status --porcelain)" ]; then tag="dev-$$sha-$$(date +%s)"; else tag="dev-$$sha"; fi; \
	 echo "$$tag" > $(TAG_FILE); echo "TAG=$$tag"; imgs=""; \
	 for s in $(SERVICES); do \
	   if [ "$$s" = dashboard ]; then docker build -t wmg/$$s:$$tag $(DASHBOARD) || exit 1; \
	   else docker build -f services/$$s/Dockerfile -t wmg/$$s:$$tag . || exit 1; fi; \
	   imgs="$$imgs wmg/$$s:$$tag"; \
	 done; \
	 k3d image import -c $(CLUSTER) $$imgs

kubeconform: ## Validate rendered charts (local + prod values) against Kubernetes schemas
	@for c in $(CHARTS); do for e in local prod; do \
	  if [ -f $$c/values-$$e.yaml ]; then echo "== $$c ($$e)"; \
	    helm template kc $$c -f $$c/values-$$e.yaml $(CHART_CHECK_SET) | kubeconform -strict -summary -ignore-missing-schemas || exit 1; \
	  fi; \
	done; done
