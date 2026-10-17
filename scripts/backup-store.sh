#!/usr/bin/env bash
# Store backup before an upgrade (DESIGN §18, D96): MariaDB dump + wp-content, streamed over `kubectl exec` into
# backups/<store>/<UTC timestamp>/ (git-ignored), with a manifest and checksums. Passwords stay inside the pods
# (read from the container's own env), never on this machine's command line.
#   scripts/backup-store.sh [store]      (default main; namespace store-<store>; prints the backup directory)
set -euo pipefail
umask 077  # a backup holds password hashes and WooCommerce API keys: readable by its owner only

STORE=${1:-${STORE:-main}}
NS=store-$STORE
TS=$(date -u +%Y%m%dT%H%M%SZ)
DIR=backups/$STORE/$TS
DB_POD=$STORE-mariadb-0
WP=deploy/$STORE-wordpress

kubectl get statefulset "$STORE-mariadb" -n "$NS" >/dev/null
mkdir -p "$DIR"
trap 'echo "backup failed: removing $DIR" >&2; rm -rf "$DIR"' ERR

echo "[1/3] database ($DB_POD)" >&2
# --add-drop-database: a restore replaces the whole database, so rows added after the backup disappear too
# shellcheck disable=SC2016  # $MARIADB_* expand inside the pod, from its own env
kubectl exec -n "$NS" "$DB_POD" -- sh -c \
  'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb-dump -uroot --single-transaction --routines --triggers \
     --add-drop-database --databases "$MARIADB_DATABASE"' | gzip -9 > "$DIR/db.sql.gz"
gzip -dc "$DIR/db.sql.gz" | tail -1 | grep -q 'Dump completed' || { echo "database dump is incomplete" >&2; false; }

echo "[2/3] wp-content ($WP)" >&2
kubectl exec -n "$NS" "$WP" -- tar -C /var/www/html -czf - wp-content > "$DIR/wp-content.tgz"
tar -tzf "$DIR/wp-content.tgz" >/dev/null

echo "[3/3] manifest" >&2
revision=$(helm history "$STORE" -n "$NS" --max 1 -o json | sed -E 's/.*"revision":([0-9]+).*/\1/')
images=$(kubectl get pods -n "$NS" -o jsonpath='{range .items[*]}{range .spec.containers[*]}{.image}{"\n"}{end}{end}' | sort -u | tr '\n' ' ')
cat > "$DIR/manifest.json" <<EOF
{"store": "$STORE", "namespace": "$NS", "createdAt": "$TS", "helmRevision": $revision, "images": "${images% }"}
EOF
(cd "$DIR" && shasum -a 256 db.sql.gz wp-content.tgz manifest.json > SHA256SUMS)
trap - ERR
echo "backup of $STORE (helm revision $revision): $(du -sh "$DIR" | cut -f1)" >&2
echo "$DIR"
