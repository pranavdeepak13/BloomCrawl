from bloomcrawl.frontend.cache_manager import CacheManager


class TestAkamaiPattern:
    def mk(self): return CacheManager(expected_queries=1000, false_positive_rate=0.001)

    def test_first_request_miss(self):
        assert self.mk().get("machine learning") is None

    def test_second_request_hits_cache(self):
        cm = self.mk()
        cm.get("deep learning")                          # first — miss, records in bloom
        cm.set("deep learning", ["r1", "r2"])
        assert cm.get("deep learning") == ["r1", "r2"]  # second — bloom hit → cache hit

    def test_roundtrip(self):
        cm = self.mk()
        cm.get("q"); cm.set("q", {"pages": [1, 2]})
        assert cm.get("q") == {"pages": [1, 2]}

    def test_independent_queries(self):
        cm = self.mk()
        cm.get("alpha"); cm.set("alpha", "res")
        assert cm.get("beta") is None  # beta never seen

    def test_bloom_records_first_request(self):
        cm = self.mk()
        assert not cm.bloom_filter.contains("new query")
        cm.get("new query")
        assert cm.bloom_filter.contains("new query")

    def test_overwrite(self):
        cm = self.mk()
        cm.get("q"); cm.set("q", "v1"); cm.set("q", "v2")
        cm.get("q")  # second bloom hit
        assert cm.get("q") == "v2"

    def test_empty_key(self):    assert self.mk().get("") is None
    def test_long_key(self):     assert self.mk().get("python " * 100) is None
