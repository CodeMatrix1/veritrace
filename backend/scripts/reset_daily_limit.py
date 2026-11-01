"""Restart today's shared limit for checks and questions.

Run from backend/ with: python -m scripts.reset_daily_limit ../.env
"""
import asyncio
import sys
from pathlib import Path

from services.db import Repository, apply_migrations


async def main() -> None:
    env = dict(
        line.split("=", 1)
        for line in Path(sys.argv[1]).read_text("utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )
    url = env["DATABASE_URL"].strip().strip('"')
    await apply_migrations(url)
    repo = await Repository.connect(url)
    await repo.reset_daily_limit()
    print("Used today:", await repo.count_audits_today())
    await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
