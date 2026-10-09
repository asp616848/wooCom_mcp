#!/bin/sh
# Idempotent WooCommerce store setup. Runs as a Helm hook Job (post-install, post-upgrade)
# with the wordpress:cli image, as uid 33 (www-data in the apache WordPress image).
# Every step checks current state first, so re-running it is always safe.
# Never prints secrets.
set -eu

step() { echo "==> $*"; }
wpa() { wp "$@" --user=admin; }   # WooCommerce CLI commands need an acting user

# --- 1. Wait for WordPress files and the database -----------------------------------------
step "1/11 waiting for wp-config.php (created by the WordPress container on first start)"
i=0
until [ -f /var/www/html/wp-config.php ]; do
  i=$((i + 1)); [ "$i" -gt 60 ] && { echo "wp-config.php not found after 300s"; exit 1; }
  sleep 5
done

step "1/11 waiting for the database"
# PHP mysqli instead of `wp db check`: the CLI image's MariaDB client may insist on TLS.
db_ready() {
  php -r '
    $h = getenv("WORDPRESS_DB_HOST"); $p = 3306;
    if (strpos($h, ":") !== false) { [$h, $p] = explode(":", $h, 2); }
    mysqli_report(MYSQLI_REPORT_OFF);
    $m = @mysqli_connect($h, getenv("WORDPRESS_DB_USER"), getenv("WORDPRESS_DB_PASSWORD"), getenv("WORDPRESS_DB_NAME"), (int) $p);
    exit($m ? 0 : 1);'
}
i=0
until db_ready; do
  i=$((i + 1)); [ "$i" -gt 24 ] && { echo "database not reachable after 120s"; exit 1; }
  sleep 5
done

# --- 2. WordPress core ----------------------------------------------------------------------
step "2/11 WordPress core"
if wp core is-installed 2>/dev/null; then
  echo "already installed"
else
  wp core install --url="$SITE_URL" --title="$STORE_TITLE" --admin_user=admin \
    --admin_password="$WP_ADMIN_PASSWORD" --admin_email="$STORE_ADMIN_EMAIL" --skip-email
fi

# --- 3. WooCommerce plugin (pinned version) ---------------------------------------------------
step "3/11 WooCommerce $WC_VERSION"
if ! wp plugin is-installed woocommerce; then
  wp plugin install woocommerce --version="$WC_VERSION" --activate
else
  current="$(wp plugin get woocommerce --field=version)"
  if [ "$current" != "$WC_VERSION" ]; then
    echo "installed $current, converging to $WC_VERSION"
    wp plugin install woocommerce --version="$WC_VERSION" --force --activate
  else
    wp plugin activate woocommerce
  fi
fi
wp wc update

# --- 4. Store options -------------------------------------------------------------------------
step "4/11 store options"
wp option update blogname "$STORE_TITLE"
wp option update timezone_string "$STORE_TIMEZONE"
wp option update woocommerce_currency "$STORE_CURRENCY"
wp option update woocommerce_default_country "$STORE_COUNTRY"
wp option update woocommerce_calc_taxes no
# New stores start in "Coming soon" mode (hidden from shoppers). Make the store live.
wp option update woocommerce_coming_soon no
wp option update woocommerce_store_pages_only no
wp option update woocommerce_onboarding_profile '{"skipped":true}' --format=json

# --- 5. Permalinks (needed for /wp-json/ REST URLs) -------------------------------------------
step "5/11 permalinks"
# The official image already ships the Apache .htaccess rewrite rules.
wp rewrite structure '/%postname%/'
wp rewrite flush

# --- 6. WooCommerce pages (shop, cart, checkout, my account) ----------------------------------
step "6/11 WooCommerce pages"
wpa wc tool run install_pages

# --- 7. Cash on delivery ----------------------------------------------------------------------
step "7/11 cash on delivery"
wpa wc payment_gateway update cod --enabled=true

# --- 8. Shipping: free shipping in the default zone (0 = "Rest of the world") -----------------
step "8/11 shipping"
if wpa wc shipping_zone_method list 0 --fields=method_id --format=csv | grep -q '^free_shipping$'; then
  echo "free shipping already configured"
else
  wpa wc shipping_zone_method create 0 --method_id=free_shipping --enabled=true
fi

# --- 9. Least-privilege user that owns the REST key -------------------------------------------
step "9/11 woo-mcp-bot user (shop_manager)"
if wp user get woo-mcp-bot --field=ID >/dev/null 2>&1; then
  echo "user exists"
else
  wp user create woo-mcp-bot woo-mcp-bot@example.com --role=shop_manager \
    --user_pass="$(head -c 24 /dev/urandom | base64)" --porcelain >/dev/null
  echo "user created"
fi

# --- 10. REST API key (from the Kubernetes Secret) --------------------------------------------
step "10/11 register WooCommerce REST key"
wp eval-file /scripts/register-wc-key.php

# --- 11. Sample products ------------------------------------------------------------------------
step "11/11 sample products"
if [ "$SAMPLE_DATA" = "true" ] && [ "$(wp post list --post_type=product --post_status=any --format=count)" = "0" ]; then
  while IFS='|' read -r name price; do
    wpa wc product create --name="$name" --type=simple --status=publish --regular_price="$price" --porcelain >/dev/null
    echo "created: $name ($price)"
  done <<'EOF'
Classic Cotton T-Shirt|799
Ceramic Coffee Mug|349
Canvas Tote Bag|499
A5 Dotted Notebook|199
Steel Water Bottle|899
EOF
else
  echo "skipped (disabled or store already has products)"
fi

echo "SETUP COMPLETE"
