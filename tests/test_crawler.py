import pytest
from bloomcrawl.crawler.crawler import Crawler
from bloomcrawl.core.bloom_filter import BloomFilter


def mock_graph(graph: dict):
    def fetcher(url):
        if url not in graph: raise ValueError(f"not in graph: {url}")
        return graph[url]
    return fetcher


class TestCrawlerCycleDetection:
    """Crawler must terminate and visit each URL exactly once despite graph cycles."""

    def test_abc_cycle(self):
        g = {
            "https://a.com": ("A", "Content A", ["https://b.com"]),
            "https://b.com": ("B", "Content B", ["https://c.com"]),
            "https://c.com": ("C", "Content C", ["https://a.com"]),
        }
        c = Crawler(["https://a.com"], fetcher=mock_graph(g), max_pages=50)
        c.crawl()
        assert c.data_store.crawled_links() == {"https://a.com", "https://b.com", "https://c.com"}
        assert c.data_store.page_count() == 3

    def test_self_loop(self):
        g = {"https://self.com": ("S", "Self content", ["https://self.com"])}
        c = Crawler(["https://self.com"], fetcher=mock_graph(g), max_pages=50)
        c.crawl()
        assert "https://self.com" in c.data_store.crawled_links()
        assert c.data_store.page_count() == 1

    def test_two_node_cycle(self):
        g = {
            "https://ping.com": ("Ping", "Ping content", ["https://pong.com"]),
            "https://pong.com": ("Pong", "Pong content", ["https://ping.com"]),
        }
        c = Crawler(["https://ping.com"], fetcher=mock_graph(g), max_pages=50)
        c.crawl()
        assert c.data_store.page_count() == 2

    def test_max_pages_respected(self):
        g = {f"https://s-{i}.com": (f"S{i}", f"C{i}", [f"https://s-{i+1}.com"]) for i in range(100)}
        g["https://s-99.com"] = ("S99", "C99", [])
        c = Crawler(["https://s-0.com"], fetcher=mock_graph(g), max_pages=10)
        c.crawl()
        assert c.data_store.page_count() <= 10

    def test_fetch_exception_skipped_gracefully(self):
        c = Crawler(["https://broken.com"], fetcher=lambda u: (_ for _ in ()).throw(ConnectionError()), max_pages=10)
        c.crawl()
        assert c.data_store.page_count() == 0

    def test_preseeded_bloom_skips_url(self):
        g = {
            "https://seed.com":    ("Seed", "Seed content", ["https://skip.com"]),
            "https://skip.com":    ("Skip", "Skip content", []),
        }
        bf = BloomFilter(100, 0.001)
        bf.add("https://skip.com")
        c = Crawler(["https://seed.com"], bloom_filter=bf, fetcher=mock_graph(g), max_pages=50)
        c.crawl()
        assert "https://skip.com" not in c.data_store.crawled_links()
        assert "https://seed.com" in c.data_store.crawled_links()
