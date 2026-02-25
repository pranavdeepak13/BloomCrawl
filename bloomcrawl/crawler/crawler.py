from __future__ import annotations
import queue, re, socket, threading, time, urllib.error, urllib.parse, urllib.request
import urllib.robotparser
from typing import Callable
from bloomcrawl.core.bloom_filter import BloomFilter
from bloomcrawl.crawler.page import Page
from bloomcrawl.crawler.page_store import PageStore
from bloomcrawl.crawler.url_frontier import URLFrontier

Fetcher = Callable[[str], tuple[str, str, list[str]]]  # url -> (title, text, child_urls)
IndexMessage = dict[str, str]

_HEADERS = {
    "User-Agent": "BloomCrawl/1.0 (+https://github.com/bloomcrawl)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Encoding": "identity",
    "Connection": "close",
}
_TIMEOUT    = 12       # seconds per request
_MAX_BYTES  = 512_000  # 512 KB per page — skip binary/video pages
_RETRY_WAIT = 1.5      # seconds before retry
_MAX_RETRIES = 2


def _resolve_url(href: str, base: str) -> str | None:
    """Resolve href relative to base. Returns None for non-HTTP or malformed URLs."""
    try:
        url    = urllib.parse.urljoin(base, href.strip())
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None
        # Drop fragments and normalise
        clean = urllib.parse.urlunparse(parsed._replace(fragment="", query=""))
        return clean
    except Exception:
        return None


def _fetch(url: str) -> tuple[str, str, list[str]]:
    """
    Production HTTP fetcher with:
    - Retry with backoff (up to _MAX_RETRIES attempts)
    - Hard byte cap (_MAX_BYTES) to skip binary payloads
    - Content-Type guard: only parse text/html
    - href extraction with absolute URL resolution
    """
    last_exc: Exception = RuntimeError("no attempts")
    for attempt in range(_MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                ct = r.headers.get_content_type()
                if ct and "html" not in ct:
                    return url, "", []          # non-HTML: index URL only
                raw = r.read(_MAX_BYTES)
            html  = raw.decode("utf-8", errors="ignore")
            m     = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
            title = m.group(1).strip() if m else url
            text  = re.sub(r"<[^>]+>", " ", html)
            hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
            children = [c for h in hrefs if (c := _resolve_url(h, url)) is not None]
            return title, text, children
        except (urllib.error.HTTPError,) as exc:
            if exc.code in (404, 410, 403):
                raise                           # permanent — don't retry
            last_exc = exc
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            last_exc = exc
        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_WAIT)
    raise last_exc


class RobotsCache:
    """Thread-safe per-domain robots.txt cache. Parsed once, then reused."""

    def __init__(self) -> None:
        self._lock  = threading.Lock()
        self._cache: dict[str, urllib.robotparser.RobotFileParser] = {}

    def allowed(self, url: str, agent: str = "BloomCrawl") -> bool:
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            if origin not in self._cache:
                rp = urllib.robotparser.RobotFileParser()
                rp.set_url(f"{origin}/robots.txt")
                try:
                    rp.read()
                except Exception:
                    rp.allow_all = True         # unreachable robots.txt → allow
                self._cache[origin] = rp
            return self._cache[origin].can_fetch(agent, url)


class RateLimiter:
    """Per-domain token bucket — prevents hammering a single host."""

    def __init__(self, requests_per_second: float = 1.0) -> None:
        self._rps   = requests_per_second
        self._lock  = threading.Lock()
        self._last: dict[str, float] = {}

    def wait(self, domain: str) -> None:
        min_gap = 1.0 / self._rps
        with self._lock:
            elapsed = time.monotonic() - self._last.get(domain, 0.0)
            gap     = min_gap - elapsed
            self._last[domain] = time.monotonic() + max(gap, 0.0)
        if gap > 0:
            time.sleep(gap)


class Crawler:
    """
    Production crawl loop with:
      BloomFilter URL dedup gate
      robots.txt compliance
      per-domain rate limiting
      per-domain URL cap (url_limit)
      absolute page budget (max_pages)
      Simhash near-duplicate content detection
      fan-out to reverse_index_queue and doc_index_queue

    Injected fetcher replaces _fetch in tests — no real HTTP in unit tests.
    """

    def __init__(
        self,
        seed_urls: list[str],
        data_store: PageStore | None = None,
        bloom_filter: BloomFilter | None = None,
        fetcher: Fetcher | None = None,
        max_pages: int = 1_000,
        url_limit: int = 100,           # max pages per seed domain
        requests_per_second: float = 1.0,
        respect_robots: bool = True,
    ) -> None:
        self.data_store   = data_store or PageStore()
        self.bloom_filter = bloom_filter or BloomFilter(max(max_pages * 4, 10_000), 0.01)
        self.frontier     = URLFrontier()
        self.fetcher      = fetcher or _fetch
        self.max_pages    = max_pages
        self.url_limit    = url_limit
        self.reverse_index_queue: queue.Queue[IndexMessage] = queue.Queue()
        self.doc_index_queue:     queue.Queue[IndexMessage] = queue.Queue()
        self._domain_count: dict[str, int] = {}
        self._robots       = RobotsCache() if respect_robots and fetcher is None else None
        self._rate_limiter = RateLimiter(requests_per_second) if fetcher is None else None
        for url in seed_urls:
            domain = self._domain(url)
            self._domain_count.setdefault(domain, 0)
            self.frontier.put(url, priority=0)

    @staticmethod
    def _domain(url: str) -> str:
        return urllib.parse.urlparse(url).netloc

    def _under_limit(self, url: str) -> bool:
        d = self._domain(url)
        return d not in self._domain_count or self._domain_count[d] < self.url_limit

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

            if self._robots and not self._robots.allowed(url):
                self.bloom_filter.add(url)      # mark seen so we don't keep checking
                self.frontier.task_done()
                continue

            if self._rate_limiter:
                self._rate_limiter.wait(self._domain(url))

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
