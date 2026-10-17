# deployer image (DESIGN §13.3, D78): helm + kubectl + the woo-store chart + the provisioning scripts.
# Build from the repo root: docker build -f deploy/deployer/Dockerfile .  (context filtered by Dockerfile.dockerignore)
# Binaries are verified against the upstream sha256 for each architecture (release.yml builds amd64 + arm64).
FROM alpine:3.24 AS tools
ARG TARGETARCH
ARG HELM_VERSION=v4.3.0
ARG KUBECTL_VERSION=v1.36.4
RUN apk add --no-cache curl
RUN set -eu; \
    arch="${TARGETARCH:-$(apk --print-arch | sed -e 's/x86_64/amd64/' -e 's/aarch64/arm64/')}"; \
    case "$arch" in \
      amd64) helm_sha=86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb; \
             kubectl_sha=8b8f088da2dab964f853b38464033b1be15ede2839eca751482357c45abdd05a ;; \
      arm64) helm_sha=31c5794dd55c66a51e6b7d2e2ac7a114ae8b1de41ff1d9ba51748ac973b06a08; \
             kubectl_sha=0ecf44450ee6063bf19dd166a103ee6df4a9034455c2abce626e6eea657d73fb ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/helm.tgz "https://get.helm.sh/helm-${HELM_VERSION}-linux-${arch}.tar.gz"; \
    echo "${helm_sha}  /tmp/helm.tgz" | sha256sum -c -; \
    tar -xzf /tmp/helm.tgz -C /tmp; \
    install -m 0755 "/tmp/linux-${arch}/helm" /usr/local/bin/helm; \
    curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${arch}/kubectl"; \
    echo "${kubectl_sha}  /usr/local/bin/kubectl" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/kubectl

FROM alpine:3.24
RUN apk add --no-cache bash ca-certificates jq openssl
COPY --from=tools /usr/local/bin/helm /usr/local/bin/kubectl /usr/local/bin/
COPY charts/woo-store /charts/woo-store
COPY --chmod=0755 deploy/deployer/scripts/ /usr/local/bin/
# Read-only root filesystem: everything writable lives under /tmp (an emptyDir in the Job).
ENV HOME=/tmp HELM_CACHE_HOME=/tmp/helm/cache HELM_CONFIG_HOME=/tmp/helm/config HELM_DATA_HOME=/tmp/helm/data \
    KUBECACHEDIR=/tmp/kube-cache
USER 65532:65532
CMD ["provision-store.sh", "--help"]
