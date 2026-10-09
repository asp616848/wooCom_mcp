<?php
/**
 * Registers (or updates) the WooCommerce REST API key whose values come from the
 * Kubernetes Secret, so the key the MCP server uses is valid in WooCommerce.
 *
 * WooCommerce stores the consumer key as an HMAC hash (wc_api_hash), the secret in plain
 * text, and the last 7 characters of the key for display. Upserting by description makes
 * this idempotent and lets a rotated Secret overwrite the old key.
 *
 * Run with: wp eval-file register-wc-key.php
 */

global $wpdb;

$description = 'woo-mcp (managed)';
$table       = $wpdb->prefix . 'woocommerce_api_keys';
$ck          = getenv('WC_CONSUMER_KEY');
$cs          = getenv('WC_CONSUMER_SECRET');

if (!$ck || !$cs) {
    WP_CLI::error('WC_CONSUMER_KEY / WC_CONSUMER_SECRET are not set');
}

$user = get_user_by('login', 'woo-mcp-bot');
if (!$user) {
    WP_CLI::error('user woo-mcp-bot does not exist');
}

$data = [
    'user_id'         => $user->ID,
    'description'     => $description,
    'permissions'     => 'read_write',
    'consumer_key'    => wc_api_hash($ck),
    'consumer_secret' => $cs,
    'truncated_key'   => substr($ck, -7),
];

$id = $wpdb->get_var($wpdb->prepare("SELECT key_id FROM {$table} WHERE description = %s", $description));
$ok = $id
    ? $wpdb->update($table, $data, ['key_id' => (int) $id])
    : $wpdb->insert($table, $data);

if ($ok === false) {
    WP_CLI::error('failed to upsert the API key: ' . $wpdb->last_error);
}

WP_CLI::success(($id ? 'updated' : 'created') . ' REST key ...' . substr($ck, -7) . ' for woo-mcp-bot');
