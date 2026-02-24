from __future__ import annotations
import threading
from bloomcrawl.crawler.page import Page
from bloomcrawl.core.simhash import Simhash

_SIM_THRESHOLD = 3  # Hamming distance <= 3 → near-duplicate


class PageStore:
    """
    In-memory NoSQL-style store for crawled pages and the pending-crawl frontier.
    Production equivalent: Cassandra or DynamoDB.
    Thread-safe via RLock.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pages: dict[str, Page] = {}
        self._crawled: set[str] = set()
        self._pending: dict[str, int] = {}  # url → priority

    def insert_crawled_link(self, page: Page) -> None:
        with self._lock:
            self._pages[page.page_id] = page
            self._crawled.add(page.url)
            self._pending.pop(page.url, None)

    def add_links_to_crawl(self, urls: list[str], priority: int = 1) -> None:
        with self._lock:
            for url in urls:
                if url not in self._crawled and url not in self._pending:
                    self._pending[url] = priority

    def crawled_links(self) -> set[str]:
        with self._lock:
            return set(self._crawled)

    def get_priority_links(self) -> list[tuple[int, str]]:
        with self._lock:
            return sorted((p, u) for u, p in self._pending.items())

    def remove_from_crawl(self, url: str) -> None:
        with self._lock:
            self._pending.pop(url, None)

    def reduce_priority(self, url: str, amount: int = 1) -> None:
        with self._lock:
            if url in self._pending:
                self._pending[url] += amount

    def get_page(self, page_id: str) -> Page | None:
        with self._lock:
            return self._pages.get(page_id)

    def crawled_similar(self, sig: Simhash) -> bool:
        """O(n) Simhash scan — acceptable at MVP scale. Production: prefix-band lookup table."""
        with self._lock:
            return any(p.signature.is_similar(sig, _SIM_THRESHOLD) for p in self._pages.values())

    def page_count(self) -> int:
        with self._lock:
            return len(self._pages)

    def to_crawl_count(self) -> int:
        with self._lock:
            return len(self._pending)
