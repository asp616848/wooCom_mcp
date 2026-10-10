-- 001: core schema (DESIGN §6). Applied once by db/migrate.py inside one transaction under the migration lock.
CREATE EXTENSION IF NOT EXISTS citext;
CREATE TYPE user_role AS ENUM ('admin','member');
CREATE TYPE user_status AS ENUM ('active','disabled');
CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), email citext UNIQUE NOT NULL, name text NOT NULL,
  role user_role NOT NULL, status user_status NOT NULL DEFAULT 'active', password_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), last_login_at timestamptz);
CREATE TABLE invites (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), email citext NOT NULL, role user_role NOT NULL,
  token_hash bytea UNIQUE NOT NULL, invited_by uuid NOT NULL REFERENCES users(id),
  expires_at timestamptz NOT NULL, accepted_at timestamptz, revoked_at timestamptz, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash bytea UNIQUE NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
  user_agent text, ip inet);
CREATE TABLE api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name text NOT NULL, prefix text UNIQUE NOT NULL, secret_hash bytea NOT NULL, created_by uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(), last_used_at timestamptz, revoked_at timestamptz, revoked_by uuid REFERENCES users(id));
CREATE INDEX api_keys_user ON api_keys(user_id) WHERE revoked_at IS NULL;
CREATE TYPE auth_type AS ENUM ('none','bearer','header');
CREATE TYPE server_source AS ENUM ('seed','manual','provisioned','hosted');
CREATE TYPE server_status AS ENUM ('unknown','connected','unreachable');
CREATE TABLE mcp_servers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), slug text UNIQUE NOT NULL CHECK (slug ~ '^[a-z][a-z0-9-]{1,29}$' AND slug !~ '--' AND slug !~ '-$'),
  name text NOT NULL, url text NOT NULL, auth_type auth_type NOT NULL DEFAULT 'none', auth_header_name text,
  credential_enc text, enabled boolean NOT NULL DEFAULT true, timeout_ms int NOT NULL DEFAULT 20000 CHECK (timeout_ms BETWEEN 1000 AND 120000),
  source server_source NOT NULL, status server_status NOT NULL DEFAULT 'unknown', last_checked_at timestamptz, last_ok_at timestamptz,
  last_error_code text, last_error text, protocol_era text, discover_result jsonb, era_verified_at timestamptz, server_info jsonb,
  created_by uuid REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE server_tools (
  server_id uuid NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE, name text NOT NULL, exposed_name text UNIQUE NOT NULL,
  title text, description text, input_schema jsonb NOT NULL, output_schema jsonb, annotations jsonb, PRIMARY KEY (server_id, name));
CREATE TABLE server_tools_meta (server_id uuid PRIMARY KEY REFERENCES mcp_servers(id) ON DELETE CASCADE, tools_hash text NOT NULL, updated_at timestamptz NOT NULL);
CREATE TABLE tool_policies (
  server_id uuid NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE, tool_name text NOT NULL, admin_only boolean NOT NULL,
  updated_by uuid REFERENCES users(id), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (server_id, tool_name));
CREATE TYPE audit_outcome AS ENUM ('success','tool_error','upstream_error','timeout','denied','unknown_tool','rate_limited');
CREATE TYPE credential_type AS ENUM ('api_key','chat_agent','oauth');
CREATE TABLE audit_log (
  id bigserial PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now(), user_id uuid, user_email text,
  credential_type credential_type, credential_id text, method text NOT NULL, server_id uuid, server_slug text,
  tool_name text, exposed_name text, outcome audit_outcome NOT NULL, duration_ms int NOT NULL, error_message text,
  args_summary jsonb, client_name text, client_mode text NOT NULL DEFAULT 'full', request_id text);
CREATE INDEX audit_ts ON audit_log(ts DESC); CREATE INDEX audit_user_ts ON audit_log(user_id, ts DESC); CREATE INDEX audit_server_ts ON audit_log(server_slug, ts DESC);
CREATE TABLE rate_counters (key text NOT NULL, window_start timestamptz NOT NULL, count int NOT NULL, PRIMARY KEY (key, window_start));
CREATE TABLE app_state (key text PRIMARY KEY, value jsonb NOT NULL);  -- e.g. 'bootstrap_done'
