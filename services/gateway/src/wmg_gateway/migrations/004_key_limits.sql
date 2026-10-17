-- SO-7 per-key rate limit override (DESIGN §5.11, D95). NULL = the gateway default (RATE_LIMIT_PER_MIN).
-- Expand-only: the P15 image never reads the column, so a Helm rollback across this migration is safe (D96).
ALTER TABLE api_keys ADD COLUMN rate_limit_per_min int CHECK (rate_limit_per_min BETWEEN 1 AND 100000);
