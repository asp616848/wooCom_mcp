-- 002: bonus store provisioning (DESIGN §6 "Later migrations", §13).
-- mcp_token_enc: the store's MCP bearer token, AES-GCM with AAD = stores.id (re-encrypted with AAD = server id when the
-- store's mcp_servers row is created).
CREATE TYPE store_status AS ENUM ('provisioning','ready','failed','deleting');
CREATE TABLE stores (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text UNIQUE NOT NULL CHECK (name ~ '^[a-z][a-z0-9]{2,15}$'),
  owner_id uuid NOT NULL REFERENCES users(id), status store_status NOT NULL, status_reason text, attempt int NOT NULL DEFAULT 1,
  namespace text NOT NULL, host text NOT NULL, mcp_token_enc text NOT NULL, server_id uuid REFERENCES mcp_servers(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), ready_at timestamptz);
CREATE INDEX stores_owner ON stores(owner_id);
CREATE INDEX stores_active ON stores(status) WHERE status IN ('provisioning','deleting');
