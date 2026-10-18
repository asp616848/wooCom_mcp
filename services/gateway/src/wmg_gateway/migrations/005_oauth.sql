-- SO-3 OAuth authorization server for MCP clients (DESIGN §15, D98). Expand-only: new tables, nothing the P16 image
-- reads is changed, so rolling back the gateway image across this migration is safe.
-- Public clients only (no client secret is stored). Codes and tokens are stored as HMAC-SHA-256 hashes (API-key pepper).
CREATE TABLE oauth_clients (
  client_id text PRIMARY KEY,
  metadata jsonb NOT NULL,                 -- RFC 7591 client information as registered
  created_at timestamptz NOT NULL DEFAULT now());

-- a pending /authorize request, waiting for the user to approve on the consent page (10 minutes)
CREATE TABLE oauth_requests (
  id text PRIMARY KEY,
  client_id text NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  params jsonb NOT NULL,                   -- the SDK's AuthorizationParams
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL);
CREATE INDEX oauth_requests_expires ON oauth_requests(expires_at);

-- single-use authorization codes (10 minutes); family_id ties a code to the tokens issued from it
CREATE TABLE oauth_codes (
  code_hash bytea PRIMARY KEY,
  client_id text NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  family_id uuid NOT NULL,
  scopes text[] NOT NULL,
  code_challenge text NOT NULL,
  redirect_uri text NOT NULL,
  redirect_uri_provided_explicitly boolean NOT NULL,
  resource text,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  used_at timestamptz);

CREATE TYPE oauth_token_kind AS ENUM ('access', 'refresh');
CREATE TABLE oauth_tokens (
  token_hash bytea PRIMARY KEY,
  kind oauth_token_kind NOT NULL,
  family_id uuid NOT NULL,                 -- one per approved authorization; rotation keeps it
  client_id text NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  scopes text[] NOT NULL,
  resource text,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz);
CREATE INDEX oauth_tokens_family ON oauth_tokens(family_id);
CREATE INDEX oauth_tokens_pair ON oauth_tokens(user_id, client_id);

-- who approved which app, for the Connected apps list
CREATE TABLE oauth_consents (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  client_id text NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  first_approved_at timestamptz NOT NULL DEFAULT now(),
  last_approved_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, client_id));
