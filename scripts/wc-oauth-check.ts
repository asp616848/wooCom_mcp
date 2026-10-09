/**
 * P3.6 spike: prove a WooCommerce REST call signed with OAuth 1.0a (one-legged, HMAC-SHA256)
 * works over plain HTTP, the way woo-mcp will call the store inside the cluster.
 *
 * Signing rules verified against woocommerce/includes/class-wc-rest-authentication.php (11.1.2):
 *  - non-HTTPS requests use OAuth 1.0a only; params may be in the query string
 *  - base URL = home URL (WP_HOME host) + request path, scheme forced to http when not HTTPS
 *  - params sorted by key (strcmp), each "key=value" RFC3986-encoded after encoding key and value
 *  - signing key = consumer_secret + "&"
 *
 * Usage (Node >= 23 runs .ts directly):
 *   kubectl port-forward -n store-main svc/main-wordpress 8081:80
 *   WC_CK=... WC_CS=... node scripts/wc-oauth-check.ts
 */
import { createHmac, randomBytes } from 'node:crypto';
import { request } from 'node:http';

const TARGET = process.env.WC_TARGET ?? 'http://127.0.0.1:8081'; // where we actually connect
const PUBLIC_HOST = process.env.WC_PUBLIC_HOST ?? 'shop.localhost'; // WP_HOME host (signature + Host header)
const ck = process.env.WC_CK;
const cs = process.env.WC_CS;
if (!ck || !cs) throw new Error('WC_CK and WC_CS must be set');

// PHP rawurlencode() equivalent (RFC 3986): encodeURIComponent leaves !'()* unencoded.
const rfc3986 = (s: string): string =>
  encodeURIComponent(s).replace(/[!'()*]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase());

function signedPath(method: string, path: string, query: Record<string, string>): string {
  const params: Record<string, string> = {
    ...query,
    oauth_consumer_key: ck!,
    oauth_nonce: randomBytes(16).toString('hex'),
    oauth_signature_method: 'HMAC-SHA256',
    oauth_timestamp: Math.floor(Date.now() / 1000).toString(),
  };
  const normalized = Object.keys(params)
    .sort() // ASCII keys: same order as PHP strcmp
    .map((k) => rfc3986(`${rfc3986(k)}=${rfc3986(params[k]!)}`))
    .join('%26');
  const baseUrl = `http://${PUBLIC_HOST}${path}`;
  const stringToSign = `${method}&${rfc3986(baseUrl)}&${normalized}`;
  const signature = createHmac('sha256', `${cs}&`).update(stringToSign).digest('base64');
  const qs = new URLSearchParams({ ...params, oauth_signature: signature });
  return `${path}?${qs.toString()}`;
}

function get(path: string, query: Record<string, string> = {}): Promise<{ status: number; body: string }> {
  const url = new URL(signedPath('GET', path, query), TARGET);
  return new Promise((resolve, reject) => {
    const req = request(url, { method: 'GET', headers: { Host: PUBLIC_HOST, Accept: 'application/json' } }, (res) => {
      let body = '';
      res.on('data', (c) => (body += c));
      res.on('end', () => resolve({ status: res.statusCode ?? 0, body }));
    });
    req.on('error', reject);
    req.end();
  });
}

const checks: Array<[string, string, Record<string, string>]> = [
  ['system_status', '/wp-json/wc/v3/system_status', {}],
  ['products (per_page=2, orderby=title)', '/wp-json/wc/v3/products', { per_page: '2', orderby: 'title', order: 'asc' }],
  ['orders', '/wp-json/wc/v3/orders', { per_page: '5' }],
];

let failed = 0;
for (const [label, path, query] of checks) {
  const { status, body } = await get(path, query);
  let summary = body.slice(0, 120);
  try {
    const json = JSON.parse(body);
    if (label === 'system_status') summary = `woocommerce ${json.environment?.version}, wp ${json.environment?.wp_version}`;
    else if (Array.isArray(json)) summary = `${json.length} item(s): ${json.map((x: { name?: string; id: number }) => x.name ?? `#${x.id}`).join(', ')}`;
    else summary = `${json.code}: ${json.message}`;
  } catch { /* non-JSON body */ }
  console.log(`${status === 200 ? 'PASS' : 'FAIL'} ${status} ${label} -> ${summary}`);
  if (status !== 200) failed++;
}

// Negative test: a tampered signature must be rejected.
{
  const url = new URL(signedPath('GET', '/wp-json/wc/v3/products', {}).replace(/oauth_signature=[^&]+/, 'oauth_signature=AAAA'), TARGET);
  const status = await new Promise<number>((resolve, reject) => {
    const req = request(url, { headers: { Host: PUBLIC_HOST } }, (res) => { res.resume(); resolve(res.statusCode ?? 0); });
    req.on('error', reject);
    req.end();
  });
  console.log(`${status === 401 ? 'PASS' : 'FAIL'} ${status} tampered signature rejected`);
  if (status !== 401) failed++;
}
process.exit(failed ? 1 : 0);
