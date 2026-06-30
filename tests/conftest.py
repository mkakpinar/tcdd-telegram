"""Shared test fixtures. The package is installed editable (`pip install -e .`),
so `import tcdd_bot` works without path hacks; we only need env vars present
because config.load_settings() reads os.environ."""

from __future__ import annotations

import os

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

import fakeredis.aioredis
import pytest
import redis.asyncio as redis_async


@pytest.fixture
async def store(monkeypatch):
    """A Store backed by fakeredis (no real Redis needed)."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_async, "from_url", lambda url, **kw: fake)
    from tcdd_bot.store import Store

    s = Store("redis://test")
    yield s
    await fake.aclose()
