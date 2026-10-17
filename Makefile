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
# every services/<name>/Dockerfile becomes image wmg/<name>, plus wmg/deployer (deploy/deployer, P14);
# override to build a subset: make images SERVICES=woo-mcp
SERVICES    ?= $(patsubst services/%/Dockerfile,%,$(wildcard services/*/Dockerfile)) deployer
IMAGE_TAG    = $(shell cat $(TAG_FILE) 2>/dev/null)
# charts refuse to render without image tags; lint/kubeconform use a placeholder
CHART_CHECK_SET := --set-string wooMcp.image.tag=check --set-string image.repository=check --set-string image.tag=check \
                   --set-string gateway.image.tag=check --set-string dashboard.image.tag=check \
                   --set-string provisioning.deployerImage.tag=check --set-string provisioning.wooMcpImage.tag=check
UPSTREAMS_NS := mcp-upstreams
SERVER_CHART := charts/mcp-server
PLATFORM_NS  := mcp-platform
HOSTED_NS    := mcp-hosted
PLATFORM_CHART := charts/mcp-platform
# seed slug  <-  namespace/secret/key holding that upstream's credential
# Image repositories: local k3d imports are wmg/<svc>; prod pulls ghcr.io/<GH_OWNER>/wmg-<svc> (D20)
IMAGE_PREFIX ?= wmg/

# ENV=prod (DESIGN §17): the VPS over the SSH tunnel; settings from .env.prod; only Helm values differ.
envprod = $(shell grep -E '^$(1)=' .env.prod 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r')
VPS_IP      ?= $(call envprod,VPS_IP)
GH_OWNER    ?= $(call envprod,GH_OWNER)
BASE_DOMAIN ?= $(subst .,-,$(VPS_IP)).sslip.io
CERT_ISSUER ?= letsencrypt-prod
VPS_KUBECONFIG := $(HOME)/.kube/wmg-vps.yaml
SSH_KEY     ?= $(HOME)/.ssh/wmg_oci
ISSUER_SET   = --set-string 'ingress.annotations.cert-manager\.io/cluster-issuer=$(CERT_ISSUER)'
STORE_ENV_SET :=
STORE_SET ?=
UPSTREAM_ENV_SET :=
PLATFORM_ENV_SET :=
KUBECTL_ENV :=
ifeq ($(ENV),prod)
export KUBECONFIG := $(VPS_KUBECONFIG)
IMAGE_PREFIX := ghcr.io/$(GH_OWNER)/wmg-
# release.yml tags images with the short sha of the commit it built; override with IMAGE_TAG=<sha>
IMAGE_TAG    = $(shell git rev-parse --short HEAD)
# macOS make 3.81: $(shell) does not see exported variables, so the kubeconfig is passed explicitly
KUBECTL_ENV := KUBECONFIG=$(VPS_KUBECONFIG)
STORE_ENV_SET = --set-string store.host=shop.$(BASE_DOMAIN) --set-string existingSecret=$(STORE)-store-credentials \
                $(ISSUER_SET)
UPSTREAM_ENV_SET = --set-string auth.existingSecret=$$name-mcp-auth
# --set-json: --set would parse the {name} placeholder as a list
PLATFORM_ENV_SET = --set-string hosts.dashboard=dashboard.$(BASE_DOMAIN) --set-string hosts.gateway=gateway.$(BASE_DOMAIN) \
                   --set-json 'provisioning.storeHostTemplate="{name}.shop.$(BASE_DOMAIN)"' $(ISSUER_SET)
endif
# API server address for the gateway + deployer NetworkPolicy egress, looked up on every install (k3d's moves when
# Docker restarts)
KUBE_API_IP  = $(shell $(KUBECTL_ENV) kubectl get endpointslices -n default -l kubernetes.io/service-name=kubernetes \
                 -o jsonpath='{.items[0].endpoints[0].addresses[0]}')

SEED_SOURCES := woo-main=$(STORE_NS)/$(STORE)-store-credentials/mcp-token \
                gst=$(UPSTREAMS_NS)/gst-mcp-auth/token \
                pincode=$(UPSTREAMS_NS)/pincode-mcp-auth/token

.PHONY: help up down reset cluster cluster-delete store store-uninstall store-test store-admin-password setup-logs \
        status deps lint chart-lint typecheck mypy test test-integration test-k8s e2e e2e-scale e2e-upgrades perf backup-store restore-store store-upgrade verify-k8s images kubeconform upstreams upstreams-uninstall \
        e2e-stores e2e-hosted platform-ns seed-credentials secrets-llm vps-bootstrap vps-tunnel vps-ssh prod-secrets prod-acme-netpol prod-up llm-models llm-smoke platform platform-uninstall admin-password

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-20s %s\n", $$1, $$2}'

up: cluster images upstreams store seed-credentials secrets-llm platform ## Create cluster (if missing), build images, install everything (chat needs GEMINI_API_KEY in .env)

down: platform-uninstall store-uninstall upstreams-uninstall ## Uninstall releases (keeps cluster, data and Secrets)

reset: cluster-delete up ## Wipe the cluster and all data, then rebuild

cluster: ## Create the local k3d cluster if it does not exist
	@k3d cluster list $(CLUSTER) >/dev/null 2>&1 || \
	  k3d cluster create $(CLUSTER) --servers 1 --agents 0 \
	    --port "80:80@loadbalancer" --port "443:443@loadbalancer" --wait
	@kubectl config use-context k3d-$(CLUSTER)

cluster-delete: ## Delete the k3d cluster (destroys all data)
	k3d cluster delete $(CLUSTER)

store: ## Install/upgrade the store (STORE=main ENV=local; woo-mcp image tag from .image-tag; extra values: STORE_SET=...)
	@test -n "$(IMAGE_TAG)" || { echo "No $(TAG_FILE) - run 'make images' first"; exit 1; }
	@kubectl create namespace $(STORE_NS) --dry-run=client -o yaml | kubectl apply -f - >/dev/null
	@# D22: the apache WordPress image needs root to bind :80, so stores run under PSA baseline (not restricted)
	@kubectl label --overwrite namespace $(STORE_NS) wmg.dev/role=store pod-security.kubernetes.io/enforce=baseline >/dev/null
	helm upgrade --install $(STORE) $(STORE_CHART) -n $(STORE_NS) \
	  -f $(STORE_CHART)/values-$(ENV).yaml --set-string wooMcp.image.repository=$(IMAGE_PREFIX)woo-mcp \
	  --set-string wooMcp.image.tag=$(IMAGE_TAG) $(STORE_ENV_SET) $(STORE_SET) --wait --timeout 10m

backup-store: ## Back up the store's database + wp-content to backups/<store>/<ts>/ (STORE=main; DESIGN §18)
	scripts/backup-store.sh $(STORE)

restore-store: ## Restore a store backup: replaces its database + wp-content (STORE=main TS=<timestamp>; asks first)
	@test -n "$(TS)" || { echo "TS=<timestamp> required: ls backups/$(STORE)"; exit 1; }
	scripts/restore-store.sh $(STORE) $(TS)

store-upgrade: ## Back up, then upgrade the store (STORE_SET='--set-string store.title=...'); rollback: helm rollback + restore-store
	$(MAKE) backup-store
	$(MAKE) store

upstreams: ## Install/upgrade gst (bearer) + pincode (X-Api-Key) in mcp-upstreams (image tag from .image-tag)
	@test -n "$(IMAGE_TAG)" || { echo "No $(TAG_FILE) - run 'make images' first"; exit 1; }
	kubectl create namespace $(UPSTREAMS_NS) --dry-run=client -o yaml | kubectl apply -f -
	kubectl label --overwrite namespace $(UPSTREAMS_NS) wmg.dev/role=upstreams pod-security.kubernetes.io/enforce=restricted
	name=gst; helm upgrade --install gst $(SERVER_CHART) -n $(UPSTREAMS_NS) -f $(SERVER_CHART)/values-$(ENV).yaml \
	  --set image.repository=$(IMAGE_PREFIX)mcp-gst --set-string image.tag=$(IMAGE_TAG) --set auth.type=bearer --set size=small \
	  --set networkPolicy.allowInternet=false $(UPSTREAM_ENV_SET) --wait --timeout 3m
	name=pincode; helm upgrade --install pincode $(SERVER_CHART) -n $(UPSTREAMS_NS) -f $(SERVER_CHART)/values-$(ENV).yaml \
	  --set image.repository=$(IMAGE_PREFIX)mcp-pincode --set-string image.tag=$(IMAGE_TAG) --set auth.type=header \
	  --set auth.headerName=X-Api-Key --set size=small --set networkPolicy.allowInternet=false $(UPSTREAM_ENV_SET) \
	  --wait --timeout 3m

upstreams-uninstall: ## Uninstall gst + pincode (credential Secrets are kept)
	-helm uninstall gst -n $(UPSTREAMS_NS)
	-helm uninstall pincode -n $(UPSTREAMS_NS)

platform-ns:
	@kubectl create namespace $(PLATFORM_NS) --dry-run=client -o yaml | kubectl apply -f - >/dev/null
	@kubectl label --overwrite namespace $(PLATFORM_NS) wmg.dev/role=platform pod-security.kubernetes.io/enforce=restricted >/dev/null
	@kubectl create namespace $(HOSTED_NS) --dry-run=client -o yaml | kubectl apply -f - >/dev/null
	@kubectl label --overwrite namespace $(HOSTED_NS) wmg.dev/role=hosted pod-security.kubernetes.io/enforce=restricted >/dev/null

seed-credentials: platform-ns ## Copy upstream credentials into mcp-platform Secrets <slug>-upstream-credential (values never printed)
	@for pair in $(SEED_SOURCES); do \
	  slug=$${pair%%=*}; src=$${pair#*=}; ns=$${src%%/*}; rest=$${src#*/}; secret=$${rest%%/*}; key=$${rest#*/}; \
	  if ! kubectl get secret $$secret -n $$ns >/dev/null 2>&1; then echo "skip $$slug: $$ns/$$secret not found"; continue; fi; \
	  kubectl get secret $$secret -n $$ns -o jsonpath="{.data.$$key}" | base64 -d | \
	    kubectl create secret generic $$slug-upstream-credential -n $(PLATFORM_NS) --from-file=credential=/dev/stdin \
	      --dry-run=client -o yaml | kubectl apply -f - >/dev/null && echo "seeded $$slug-upstream-credential"; \
	done

secrets-llm: platform-ns ## Create Secret platform-llm from GEMINI_API_KEY (+ optional GEMINI_API_KEY_2 fallback, D92) in .env
	@key=$$(grep -E '^GEMINI_API_KEY=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r'); \
	if [ -z "$$key" ]; then echo "WARNING: GEMINI_API_KEY not set in .env - chat will be unavailable"; exit 0; fi; \
	key2=$$(grep -E '^GEMINI_API_KEY_2=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r'); \
	dir=$$(mktemp -d); trap 'rm -rf "$$dir"' EXIT; umask 077; \
	printf '%s' "$$key" > "$$dir/api-key"; \
	if [ -n "$$key2" ]; then printf '%s' "$$key2" > "$$dir/api-key-fallback"; fi; \
	kubectl create secret generic platform-llm -n $(PLATFORM_NS) --from-file="$$dir" \
	  --dry-run=client -o yaml | kubectl apply -f - >/dev/null && echo "platform-llm Secret updated"

# GEMINI_API_KEY from .env goes into the child's environment only; recipes are silent so it is never echoed
LLM_KEY_ENV = GEMINI_API_KEY="$$(grep -E '^GEMINI_API_KEY=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r')"

llm-models: ## Show which candidate Gemini models the key in .env can use (PLAN P10.1)
	@$(LLM_KEY_ENV) $(UV_RUN) python scripts/llm_check.py models

llm-smoke: ## One function-calling round trip per candidate model; first pass wins (MODEL=... to test one)
	@$(LLM_KEY_ENV) $(UV_RUN) python scripts/llm_check.py smoke $(if $(MODEL),--model $(MODEL))

platform: platform-ns ## Install/upgrade gateway + dashboard + Postgres + store provisioning (image tags from .image-tag; SCALE=1: 2 gateways + PDB)
	@test -n "$(IMAGE_TAG)" || { echo "No $(TAG_FILE) - run 'make images' first"; exit 1; }
	@test -n "$(KUBE_API_IP)" || { echo "Could not look up the Kubernetes API address"; exit 1; }
	helm upgrade --install platform $(PLATFORM_CHART) -n $(PLATFORM_NS) -f $(PLATFORM_CHART)/values-$(ENV).yaml \
	  $(if $(SCALE),-f $(PLATFORM_CHART)/values-scale.yaml) \
	  --set-string gateway.image.repository=$(IMAGE_PREFIX)gateway --set-string gateway.image.tag=$(IMAGE_TAG) \
	  --set-string dashboard.image.repository=$(IMAGE_PREFIX)dashboard --set-string dashboard.image.tag=$(IMAGE_TAG) \
	  --set-string provisioning.deployerImage.repository=$(IMAGE_PREFIX)deployer \
	  --set-string provisioning.deployerImage.tag=$(IMAGE_TAG) \
	  --set-string provisioning.wooMcpImage.repository=$(IMAGE_PREFIX)woo-mcp \
	  --set-string provisioning.wooMcpImage.tag=$(IMAGE_TAG) \
	  --set 'networkPolicy.kubeApiCIDRs={$(KUBE_API_IP)/32}' $(PLATFORM_ENV_SET) --wait --timeout 5m

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

e2e: ## DoD acceptance suite + isolation checks against the running cluster (TESTING §4, §5; make up first)
	$(UV_RUN) pytest tests/e2e -m "e2e and not bonus" -v --durations=0

e2e-stores: ## Bonus store provisioning B-01..B-07 against the running cluster (TESTING §6; ~15 min, creates paris/tokyo)
	$(UV_RUN) pytest tests/e2e/test_stores.py -m bonus -v --durations=0

e2e-hosted: ## SO-5 hosted MCP server SO5-01 against the running cluster (TESTING §6; ~3 min, deploys pincode-hosted)
	$(UV_RUN) pytest tests/e2e/test_hosted.py -m bonus -v --durations=0

e2e-scale: ## SO-7 two replicas: NFR-P1/P2 in-cluster, leader kills under load, PDB, rate limit (`make platform SCALE=1` first; ~4 min)
	$(UV_RUN) pytest tests/e2e/test_scale.py -m bonus -v -rP --durations=0

e2e-upgrades: ## SO9-01 helm upgrade + rollback of gateway, pincode and store (backup/restore) on the local cluster (~6 min)
	$(UV_RUN) pytest tests/e2e/test_upgrades.py -m bonus -v -rP --durations=0

perf: ## TESTING §7 autocannon run through the ingress (PERF_AUTH='Authorization=Bearer <key>' RUN=list|call)
	tests/perf/run.sh $(or $(RUN),list) 30 0 | tail -1

# Deployments/StatefulSets: requests, limits, liveness, readiness. Jobs run to completion: requests + limits only.
VERIFY_JQ := '[.items[] | select(.metadata.namespace | test("^(store-|mcp-)")) | .kind as $$k \
  | "\(.kind) \(.metadata.namespace)/\(.metadata.name)" as $$w | .spec.template.spec.containers[] \
  | {w: "\($$w) [\(.name)]", missing: ([ (if .resources.requests == null then "requests" else empty end), \
      (if .resources.limits == null then "limits" else empty end), \
      (if $$k != "Job" and .livenessProbe == null then "liveness" else empty end), \
      (if $$k != "Job" and .readinessProbe == null then "readiness" else empty end) ])}] \
  | (.[] | "\(if (.missing | length) == 0 then "ok  " else "FAIL" end) \(.w)\(if (.missing | length) > 0 then " missing: \(.missing | join(", "))" else "" end)"), \
    (if all(.[]; (.missing | length) == 0) then "verify-k8s: all workloads OK" else error("verify-k8s: workloads above are missing settings") end)'

verify-k8s: ## Every store/platform workload container has requests, limits and probes (Jobs: requests + limits)
	@kubectl get deploy,statefulset,job -A -o json | jq -r $(VERIFY_JQ)

test-k8s: ## Network isolation, PSA, RBAC and live SSRF checks against the running cluster (TESTING §4)
	$(UV_RUN) pytest tests/e2e/test_k8s.py -m e2e

images: ## Build service images (tag dev-<sha>[-<ts> if dirty] -> .image-tag) and import them into k3d
	@if [ -z "$(strip $(SERVICES))" ]; then echo "No services/*/Dockerfile yet - nothing to build"; exit 0; fi; \
	 sha=$$(git rev-parse --short HEAD); \
	 if [ -n "$$(git status --porcelain)" ]; then tag="dev-$$sha-$$(date +%s)"; else tag="dev-$$sha"; fi; \
	 echo "$$tag" > $(TAG_FILE); echo "TAG=$$tag"; imgs=""; \
	 for s in $(SERVICES); do \
	   if [ "$$s" = dashboard ]; then docker build -t wmg/$$s:$$tag $(DASHBOARD) || exit 1; \
	   elif [ "$$s" = deployer ]; then docker build -f deploy/deployer/Dockerfile -t wmg/$$s:$$tag . || exit 1; \
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

# ---------------------------------------------------------------------------------------------- VPS (P13)

vps-bootstrap: ## VPS: firewall, k3s, kubeconfig (~/.kube/wmg-vps.yaml), tunnel, cert-manager + issuers (VPS_IP from .env.prod)
	@test -n "$(VPS_IP)" || { echo "Set VPS_IP in .env.prod"; exit 1; }
	SSH_KEY=$(SSH_KEY) deploy/k3s/vps-bootstrap.sh $(VPS_IP)

vps-tunnel: ## VPS: (re)open the SSH tunnel localhost:16443 -> VPS API (6443 is never public)
	@nc -z 127.0.0.1 16443 2>/dev/null && echo "tunnel already open" || \
	  ssh -i $(SSH_KEY) -f -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 16443:127.0.0.1:6443 ubuntu@$(VPS_IP)

vps-ssh: ## VPS: SSH shell
	ssh -i $(SSH_KEY) ubuntu@$(VPS_IP)

prod-secrets: ## VPS: create namespaces + Secrets once (scripts/prod-secrets.sh; values never printed)
	KUBECONFIG=$(VPS_KUBECONFIG) scripts/prod-secrets.sh .env.prod

prod-acme-netpol: ## VPS: let Traefik reach cert-manager HTTP-01 solver pods in mcp-platform (stores: woo-store chart)
	KUBECONFIG=$(VPS_KUBECONFIG) kubectl apply -n $(PLATFORM_NS) -f deploy/k3s/acme-solver-netpol.yaml

prod-up: ## VPS: secrets, upstreams, store, credentials, platform from GHCR images (IMAGE_TAG=<sha>, CERT_ISSUER=letsencrypt-staging to test)
	$(MAKE) ENV=prod prod-secrets prod-acme-netpol upstreams store seed-credentials platform
