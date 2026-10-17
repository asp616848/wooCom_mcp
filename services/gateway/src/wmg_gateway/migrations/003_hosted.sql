-- SO-5 hosted MCP servers (DESIGN §14, D94). One row per hosted server, next to its mcp_servers row
-- (source 'hosted'); the leader's HostedReconciler drives deploy_status through deployer Jobs.
CREATE TABLE hosted_deployments (
  server_id uuid PRIMARY KEY REFERENCES mcp_servers(id) ON DELETE CASCADE,
  image text NOT NULL,
  port int NOT NULL CHECK (port BETWEEN 1024 AND 65535),
  path text NOT NULL,
  env jsonb NOT NULL DEFAULT '{}',
  secret_env_enc text,
  secret_env_keys text[] NOT NULL DEFAULT '{}',
  size text NOT NULL CHECK (size IN ('small','medium')),
  allow_internet boolean NOT NULL DEFAULT false,
  deploy_status text NOT NULL CHECK (deploy_status IN ('deploying','deployed','failed','removing')),
  deploy_reason text,
  attempt int NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deployed_at timestamptz
);
CREATE INDEX hosted_active ON hosted_deployments(deploy_status) WHERE deploy_status IN ('deploying','removing');
