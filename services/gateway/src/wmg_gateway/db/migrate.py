"""Numbered `.sql` migrations (D72).

All pending files run in ONE transaction under a Postgres advisory lock, so two gateway replicas starting
together can't both migrate. Applied versions are kept in `schema_migrations`.
"""

from collections.abc import Awaitable, Callable
from importlib import resources
from typing import Any

from wmg_common.logging import get_logger

log = get_logger(__name__)

# Arbitrary constant shared by every replica: "the migration lock".
MIGRATION_LOCK_ID = 7_301_001


def migration_files() -> list[tuple[str, str]]:
    folder = resources.files("wmg_gateway").joinpath("migrations")
    files = sorted((f for f in folder.iterdir() if f.name.endswith(".sql")), key=lambda f: f.name)
    return [(f.name.removesuffix(".sql"), f.read_text(encoding="utf-8")) for f in files]


async def migrate(pool: Any, after: Callable[[Any], Awaitable[object]] | None = None) -> list[str]:
    """Apply pending migrations, then run `after(conn)` (bootstrap) inside the same locked transaction."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", MIGRATION_LOCK_ID)
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        applied: list[str] = []
        for version, sql in migration_files():
            if version in done:
                continue
            await conn.execute(sql)
            await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
            applied.append(version)
        log.info("migrations checked", applied=applied, already_applied=sorted(done))
        if after is not None:
            await after(conn)
    return applied
