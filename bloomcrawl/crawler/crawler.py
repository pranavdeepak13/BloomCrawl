from __future__ import annotations
import queue, re, urllib.request, urllib.parse
from typing import Callable
from bloomcrawl.core.bloom_filter import BloomFilter
from bloomcrawl.crawler.page import Page
from bloomcrawl.crawler.page_store import PageStore
from bloomcrawl.crawler.url_frontier import URLFrontier

Fetcher = Callable[[str], tuple[str, str, list[str]]]  # url -> (title, content, child_urls)
IndexMessage = dict[str, str]


def _resolve_url(href: str, base: str) -> str | None:
    """Resolve href relative to base, return None if non-HTTP or malformed."""
    try:
        url = urllib.parse.urljoin(base, href.strip())
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None
        # Drop fragments; normalise trailing slash on root paths
        return urllib.parse.urlunparse(parsed._replace(fragment=""))
    except Exception:
        return None


def _fetch(url: str) -> tuple[str, str, list[str]]:
    """Real HTTP fetcher. Returns (title, text_content, absolute_child_urls)."""
    req = urllib.request.Request(url, headers={"User-Agent": "BloomCrawl/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read().decode("utf-8", errors="ignore")
    m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    title = m.group(1).strip() if m else url
    text  = re.sub(r"<[^>]+>", " ", html)
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    children = [c for h in hrefs if (c := _resolve_url(h, url)) is not None]
    return title, text, children


class Crawler:
    """
    Crawl loop: BloomFilter gate -> fetch -> Simhash dedup -> commit -> index queues.

    Per-URL flow:
        1. Pop from URLFrontier (priority queue, seeds at 0, children at 1).
        2. BloomFilter.contains()  — skip if probably seen.
        3. HTTP fetch via injected fetcher (swap _fetch for mock in tests).
        4. PageStore.crawled_similar() — skip near-duplicate content.
        5. Commit: BloomFilter.add() + PageStore.insert_crawled_link().
        6. Enqueue child URLs (capped by url_limit per seed domain).
        7. Publish IndexMessage to reverse_index_queue and doc_index_queue.

    url_limit caps the total URLs crawled per originating seed domain.
    max_pages caps the absolute total across all domains.
    """

    def __init__(
        self,
        seed_urls: list[str],
        data_store: PageStore | None = None,
        bloom_filter: BloomFilter | None = None,
        fetcher: Fetcher | None = None,
        max_pages: int = 1000,
        url_limit: int = 50,          # max URLs crawled per seed domain
    ) -> None:
        self.data_store   = data_store or PageStore()
        self.bloom_filter = bloom_filter or BloomFilter(max_pages * 2, 0.01)
        self.frontier     = URLFrontier()
        self.fetcher      = fetcher or _fetch
        self.max_pages    = max_pages
        self.url_limit    = url_limit
        self.reverse_index_queue: queue.Queue[IndexMessage] = queue.Queue()
        self.doc_index_queue:     queue.Queue[IndexMessage] = queue.Queue()
        # domain -> crawled count; seeded from the seed URL's netloc
        self._domain_count: dict[str, int] = {}
        for url in seed_urls:
            domain = urllib.parse.urlparse(url).netloc
            self._domain_count.setdefault(domain, 0)
            self.frontier.put(url, priority=0)

    def _domain(self, url: str) -> str:
        return urllib.parse.urlparse(url).netloc

    def _under_limit(self, url: str) -> bool:
        d = self._domain(url)
        # Only count against seeds' domains; unknown domains are uncapped
        if d not in self._domain_count:
            return True
        return self._domain_count[d] < self.url_limit

    def crawl(self) -> None:
        crawled = 0
        while not self.frontier.empty() and crawled < self.max_pages:
            try:
                url = self.frontier.get(block=False)
            except queue.Empty:
                break

            if self.bloom_filter.contains(url):
                self.frontier.task_done()
                continue

            if not self._under_limit(url):
                self.frontier.task_done()
                continue

            try:
                title, content, children = self.fetcher(url)
            except Exception:
                self.frontier.task_done()
                continue

            page = Page.from_raw(url, title, content, children)

            if self.data_store.crawled_similar(page.signature):
                self.bloom_filter.add(url)
                self.frontier.task_done()
                continue

            self.bloom_filter.add(url)
            self.data_store.insert_crawled_link(page)
            d = self._domain(url)
            if d in self._domain_count:
                self._domain_count[d] += 1
            crawled += 1

            self.data_store.add_links_to_crawl(children, priority=1)
            for child in children:
                self.frontier.put(child, priority=1)

            msg: IndexMessage = {
                "page_id": page.page_id, "url": page.url,
                "title": page.title, "snippet": page.snippet, "content": page.content,
            }
            self.reverse_index_queue.put(msg)
            self.doc_index_queue.put(msg)
            self.frontier.task_done()
