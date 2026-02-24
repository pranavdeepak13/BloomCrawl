from __future__ import annotations
import statistics, threading, time
from collections import defaultdict


class ReverseIndex:
    """
    Inverted index: word → [(page_id, score)], sorted by score desc.
    Backed by dict — interface swap-ready for Redis (HSET/ZADD).
    Thread-safe via RLock.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._idx: dict[str, list[tuple[str, float]]] = defaultdict(list)

    def add(self, word: str, page_id: str, score: float = 1.0) -> None:
        with self._lock:
            self._idx[word].append((page_id, score))

    def lookup(self, word: str) -> list[tuple[str, float]]:
        with self._lock:
            return sorted(self._idx.get(word, []), key=lambda x: x[1], reverse=True)

    def lookup_multi(self, words: list[str]) -> list[tuple[str, float]]:
        """Boolean AND across words, scored by sum."""
        if not words:
            return []
        with self._lock:
            sets = [set(pid for pid, _ in self._idx.get(w, [])) for w in words]
            common = sets[0].intersection(*sets[1:])
            scores: dict[str, float] = defaultdict(float)
            for w in words:
                for pid, s in self._idx.get(w, []):
                    if pid in common:
                        scores[pid] += s
            return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    def word_count(self) -> int:
        with self._lock:
            return len(self._idx)

    def benchmark_lookup(self, word: str, n: int = 1000) -> dict[str, float]:
        """Return p50/p95/p99 latency in ms over n lookups."""
        lats = []
        for _ in range(n):
            t0 = time.perf_counter()
            self.lookup(word)
            lats.append((time.perf_counter() - t0) * 1000)
        lats.sort()
        return {
            "p50_ms": statistics.median(lats),
            "p95_ms": lats[int(0.95 * n)],
            "p99_ms": lats[int(0.99 * n)],
        }
