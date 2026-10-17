#!/usr/bin/env bash
# Restore a store from scripts/backup-store.sh output (DESIGN §18, D96): replaces the whole database and
# wp-content. WooCommerce's own DB migrations aren't reversible, so after a failed plugin/WordPress bump the
# rollback is: restore the backup taken before it, then `helm rollback` the release.
#   scripts/restore-store.sh <store> <timestamp> [--yes]
set -euo pipefail

STORE=${1:?usage: restore-store.sh <store> <timestamp> [--yes]}
TS=${2:?usage: restore-store.sh <store> <timestamp> [--yes]}
NS=store-$STORE
DIR=backups/$STORE/$TS
DB_POD=$STORE-mariadb-0
WP=deploy/$STORE-wordpress

[ -d "$DIR" ] || { echo "no backup at $DIR (ls backups/$STORE)" >&2; exit 1; }
(cd "$DIR" && shasum -a 256 -c --quiet SHA256SUMS) || { echo "checksums don't match: not restoring" >&2; exit 1; }
if [ "${3:-}" != "--yes" ]; then
  read -r -p "Replace the database and wp-content of store '$STORE' with the backup from $TS? [y/N] " answer
  [ "$answer" = y ] || [ "$answer" = Y ] || { echo "cancelled" >&2; exit 1; }
fi

echo "[1/2] database ($DB_POD)" >&2
# shellcheck disable=SC2016  # $MARIADB_* expand inside the pod, from its own env
gzip -dc "$DIR/db.sql.gz" | kubectl exec -i -n "$NS" "$DB_POD" -- sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb -uroot'

echo "[2/2] wp-content ($WP)" >&2
# unpack next to the live folder, then swap, so a failed upload never leaves half a wp-content
kubectl exec -i -n "$NS" "$WP" -- sh -c \
  'set -e; cd /var/www/html; rm -rf .restore; mkdir .restore; tar -C .restore -xzf -;
   rm -rf wp-content.old; mv wp-content wp-content.old; mv .restore/wp-content wp-content;
   rm -rf wp-content.old .restore' < "$DIR/wp-content.tgz"

echo "restored $STORE from $TS" >&2
