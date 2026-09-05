"""Forward-only, versioned migrations under a PostgreSQL advisory lock."""
import hashlib
from pathlib import Path
import asyncpg
import config


async def migrate():
    connection = await asyncpg.connect(config.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await connection.execute("SELECT pg_advisory_lock(8873771495)")
        await connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        for path in sorted(Path(__file__).parent.glob("*.sql")):
            sql = path.read_text(encoding="utf-8-sig")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            old = await connection.fetchval("SELECT checksum FROM schema_migrations WHERE version=$1", path.name)
            if old:
                if old != checksum:
                    raise RuntimeError(f"Applied migration changed: {path.name}")
                continue
            async with connection.transaction():
                await connection.execute(sql)
                await connection.execute("INSERT INTO schema_migrations(version, checksum) VALUES($1,$2)", path.name, checksum)
    finally:
        await connection.close()
