from __future__ import annotations
import json
from typing import Any
from bloomcrawl.core.bloom_filter import BloomFilter


class CacheManager:
    """
    Akamai two-tier cache: BloomFilter admission gate → Redis (or local dict).

    Logic per query:
    BloomFilter miss → "definitely first request" → record + return None.
    BloomFilter hit  → "probably seen" → check Redis/local.
    Only queries seen ≥2 times enter Redis — avoids one-shot cache pollution.
    Inject a redis.Redis() instance for production; omit for in-process mock.
    """

    def __init__(
        self,
        redis_client: Any = None,
        expected_queries: int = 500_000,
        false_positive_rate: float = 0.01,
        ttl_seconds: int = 300,
    ) -> None:
        self._bloom = BloomFilter(expected_queries, false_positive_rate)
        self._redis = redis_client
        self._ttl   = ttl_seconds
        self._local: dict[str, Any] = {}

    def get(self, query: str) -> Any | None:
        seen = self._bloom.contains(query)
        self._bloom.add(query)
        return self._read(query) if seen else None

    def set(self, query: str, result: Any) -> None:
        if self._redis is not None:
            self._redis.setex(query, self._ttl, json.dumps(result))
        else:
            self._local[query] = result

    def _read(self, query: str) -> Any | None:
        if self._redis is not None:
            raw = self._redis.get(query)
            return json.loads(raw) if raw is not None else None
        return self._local.get(query)

    @property
    def bloom_filter(self) -> BloomFilter:
        return self._bloom
