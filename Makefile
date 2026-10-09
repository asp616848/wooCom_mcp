# One-command local setup. Run `make help` for targets.

CLUSTER     ?= woo-mcp
STORE       ?= main
STORE_NS    ?= store-$(STORE)
STORE_CHART := charts/woo-store
ENV         ?= local

.PHONY: help up down reset cluster cluster-delete store store-uninstall store-test store-admin-password setup-logs status lint

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n", $$1, $$2}'

up: cluster store ## Create cluster (if missing) and install everything

down: store-uninstall ## Uninstall releases (keeps cluster, data and Secrets)

reset: cluster-delete up ## Wipe the cluster and all data, then rebuild

cluster: ## Create the local k3d cluster if it does not exist
	@k3d cluster list $(CLUSTER) >/dev/null 2>&1 || \
	  k3d cluster create $(CLUSTER) --servers 1 --agents 0 \
	    --port "80:80@loadbalancer" --port "443:443@loadbalancer" --wait
	@kubectl config use-context k3d-$(CLUSTER)

cluster-delete: ## Delete the k3d cluster (destroys all data)
	k3d cluster delete $(CLUSTER)

store: ## Install/upgrade the store (STORE=main ENV=local)
	helm upgrade --install $(STORE) $(STORE_CHART) -n $(STORE_NS) --create-namespace \
	  -f $(STORE_CHART)/values-$(ENV).yaml --wait --timeout 10m

store-uninstall: ## Uninstall the store release (PVCs and Secret are kept)
	-helm uninstall $(STORE) -n $(STORE_NS)

store-test: ## Run the store's helm tests
	helm test $(STORE) -n $(STORE_NS) --logs

store-admin-password: ## Print the WordPress admin password (user: admin)
	@kubectl get secret $(STORE)-store-credentials -n $(STORE_NS) -o jsonpath='{.data.wordpress-admin-password}' | base64 -d; echo

setup-logs: ## Show logs of the last store setup Job
	kubectl logs -n $(STORE_NS) job/$(STORE)-setup --tail=200

status: ## Show store resources
	kubectl get all,pvc,ingress -n $(STORE_NS)

lint: ## Lint charts
	helm lint $(STORE_CHART) -f $(STORE_CHART)/values-$(ENV).yaml
