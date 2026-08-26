"""
Event bus for inter-agent + agent→frontend communication.

Two backends:
- LOCAL_MODE  → in-process asyncio pub/sub (no Redis). The API, WebSocket, and
  the in-process pipeline all share one event loop, so this is sufficient.
- production  → Redis Pub/Sub (works across separate API + Celery worker procs).
"""
import asyncio
import json
import logging
from typing import Any, AsyncIterator, Dict, Set

from backend.config import settings

logger = logging.getLogger(__name__)


# ── In-memory broker (singleton, LOCAL_MODE) ──────────────────────────────────

class _InMemoryBroker:
    def __init__(self) -> None:
        self._channels: Dict[str, Set[asyncio.Queue]] = {}

    async def publish(self, channel: str, payload: Dict[str, Any]) -> None:
        for q in list(self._channels.get(channel, ())):
            await q.put(payload)

    async def subscribe(self, channel: str) -> AsyncIterator[Dict[str, Any]]:
        q: asyncio.Queue = asyncio.Queue()
        self._channels.setdefault(channel, set()).add(q)
        try:
            while True:
                yield await q.get()
        finally:
            subs = self._channels.get(channel)
            if subs:
                subs.discard(q)
                if not subs:
                    self._channels.pop(channel, None)


_memory_broker = _InMemoryBroker()


class EventBus:
    """Unified pub/sub facade — picks in-memory or Redis based on LOCAL_MODE."""

    def __init__(self) -> None:
        self._redis = None

    async def _get_redis(self):
        import redis.asyncio as aioredis
        if self._redis is None:
            self._redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._redis

    async def publish(self, channel: str, payload: Dict[str, Any]) -> None:
        if settings.LOCAL_MODE:
            await _memory_broker.publish(channel, payload)
            return
        r = await self._get_redis()
        await r.publish(channel, json.dumps(payload))

    async def subscribe(self, channel: str) -> AsyncIterator[Dict[str, Any]]:
        if settings.LOCAL_MODE:
            async for item in _memory_broker.subscribe(channel):
                yield item
            return
        r = await self._get_redis()
        pubsub = r.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for raw in pubsub.listen():
                if raw["type"] == "message":
                    yield json.loads(raw["data"])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None


async def broadcast_progress(
    project_id: str, agent_name: str, pct: int, message: str = "", **extra: Any
) -> None:
    """Publish a progress event the WebSocket relays to the frontend."""
    bus = EventBus()
    payload = {
        "event": "agent.progress",
        "project_id": project_id,
        "agent": agent_name,
        "progress_pct": pct,
        "message": message,
    }
    payload.update(extra)
    await bus.publish(f"project:{project_id}", payload)
    await bus.close()
