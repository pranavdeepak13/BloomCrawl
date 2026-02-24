from collections import Counter
from typing import Iterable


class DedupJob:
    """
    Offline batch deduplication — MapReduce analogy via collections.Counter.
    BloomFilter = online real-time gate.  DedupJob = scheduled batch cleanup.
    map: url→1  |  reduce: sum  |  filter: keep count==1 (truly unique).
    """

    @staticmethod
    def deduplicate(urls: Iterable[str]) -> list[str]:
        return [u for u, c in Counter(urls).items() if c == 1]

    @staticmethod
    def find_duplicates(urls: Iterable[str]) -> dict[str, int]:
        return {u: c for u, c in Counter(urls).items() if c > 1}

    @staticmethod
    def unique_urls(urls: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(urls))
