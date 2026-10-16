#!/usr/bin/env bash
# VPS bootstrap (DESIGN §17, D19). Run from the laptop:  deploy/k3s/vps-bootstrap.sh <public-ip>
#  1. host firewall: allow 80/443 and pod/service traffic (Oracle Ubuntu images REJECT everything else), persist
#  2. k3s v1.36.4+k3s1 (API on 127.0.0.1:6443 of the VPS; 6443 is never opened publicly)
#  3. kubeconfig -> ~/.kube/wmg-vps.yaml pointing at an SSH tunnel (localhost:16443 -> VPS 127.0.0.1:6443)
#  4. cert-manager v1.21.2 (Helm) + ClusterIssuers letsencrypt-staging / letsencrypt-prod
# Idempotent: safe to re-run.
set -euo pipefail

IP=${1:?usage: vps-bootstrap.sh <public-ip>}
SSH_USER=${SSH_USER:-ubuntu}
SSH_KEY=${SSH_KEY:-$HOME/.ssh/wmg_oci}
K3S_VERSION=v1.36.4+k3s1
CERT_MANAGER_VERSION=v1.21.2
TUNNEL_PORT=${TUNNEL_PORT:-16443}
KCFG=${KCFG:-$HOME/.kube/wmg-vps.yaml}
HERE=$(cd "$(dirname "$0")" && pwd)
SSH=(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$SSH_USER@$IP")

echo "== 1/4 host firewall"
"${SSH[@]}" sudo bash -s <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
dpkg -s iptables-persistent >/dev/null 2>&1 || {
  echo iptables-persistent iptables-persistent/autosave_v4 boolean false | debconf-set-selections
  echo iptables-persistent iptables-persistent/autosave_v6 boolean false | debconf-set-selections
  apt-get update -qq && apt-get install -y -qq iptables-persistent >/dev/null
}
add() { iptables -C "$@" 2>/dev/null || iptables -I "$@"; }
add INPUT -p tcp -m multiport --dports 80,443 -m conntrack --ctstate NEW -j ACCEPT
add INPUT -s 10.42.0.0/16 -j ACCEPT   # k3s pods -> node (API server, kubelet, DNS)
add INPUT -s 10.43.0.0/16 -j ACCEPT   # k3s services
# Oracle's image rejects all forwarded traffic, which breaks Service routing to pods.
while iptables -C FORWARD -j REJECT --reject-with icmp-host-prohibited 2>/dev/null; do
  iptables -D FORWARD -j REJECT --reject-with icmp-host-prohibited
done
# Save before k3s adds its own chains (k3s recreates those on boot).
iptables-save | grep -v -E 'KUBE-|CNI-|FLANNEL|flannel' > /etc/iptables/rules.v4
REMOTE

echo "== 2/4 k3s $K3S_VERSION"
"${SSH[@]}" "sudo test -x /usr/local/bin/k3s && k3s --version | grep -q '$K3S_VERSION'" 2>/dev/null || \
  "${SSH[@]}" "curl -sfL https://get.k3s.io | sudo INSTALL_K3S_VERSION=$K3S_VERSION sh -s - --write-kubeconfig-mode 600"
"${SSH[@]}" "sudo k3s kubectl wait --for=condition=Ready node --all --timeout=180s" >/dev/null

echo "== 3/4 kubeconfig + tunnel"
mkdir -p "$(dirname "$KCFG")"
umask 077
"${SSH[@]}" sudo cat /etc/rancher/k3s/k3s.yaml \
  | sed -e "s#https://127.0.0.1:6443#https://127.0.0.1:$TUNNEL_PORT#" -e 's/: default$/: wmg-vps/' > "$KCFG"
nc -z 127.0.0.1 "$TUNNEL_PORT" 2>/dev/null || \
  ssh -i "$SSH_KEY" -f -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    -L "$TUNNEL_PORT:127.0.0.1:6443" "$SSH_USER@$IP"
export KUBECONFIG=$KCFG
kubectl get nodes -o wide

echo "== 4/4 cert-manager $CERT_MANAGER_VERSION + ClusterIssuers"
helm upgrade --install cert-manager cert-manager --repo https://charts.jetstack.io --version "$CERT_MANAGER_VERSION" \
  -n cert-manager --create-namespace --set crds.enabled=true --wait --timeout 5m
kubectl apply -f "$HERE/cluster-issuers.yaml"
echo "done. Use: export KUBECONFIG=$KCFG   (tunnel: make vps-tunnel)"
