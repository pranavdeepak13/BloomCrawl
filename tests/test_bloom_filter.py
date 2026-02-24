import math
import pytest
from bloomcrawl.core.bloom_filter import BloomFilter

URL = lambda i: f"https://test-{i}.example.com/page/{i}"
PROBE = lambda i: f"https://probe-{i}.example.com/p/{i}"


# ── Construction ─────────────────────────────────────────────────────────────

class TestConstruction:
    def test_m_formula(self):
        bf = BloomFilter(1000, 0.01)
        assert bf.bit_array_size == math.ceil(-1000 * math.log(0.01) / math.log(2) ** 2) + 1

    def test_k_formula(self):
        bf = BloomFilter(1000, 0.01)
        assert bf.num_hash_functions == max(1, min(20, round((bf.bit_array_size / 1000) * math.log(2))))

    def test_larger_n_bigger_m(self):
        assert BloomFilter(1_000_000, 0.01).bit_array_size > BloomFilter(1_000, 0.01).bit_array_size

    def test_stricter_p_bigger_m(self):
        assert BloomFilter(1000, 0.001).bit_array_size > BloomFilter(1000, 0.10).bit_array_size

    def test_initial_count_zero(self):
        assert BloomFilter(100, 0.01).item_count == 0

    def test_properties_match_args(self):
        bf = BloomFilter(500, 0.05)
        assert bf.expected_items == 500 and bf.target_false_positive_rate == 0.05

    @pytest.mark.parametrize("n,p,match", [
        (0, 0.01, "expected_items"), (-1, 0.01, "expected_items"),
        (100, 0.0, "false_positive_rate"), (100, 1.0, "false_positive_rate"),
        (100, 1.5, "false_positive_rate"), (100, -0.1, "false_positive_rate"),
    ])
    def test_invalid_args_raise(self, n, p, match):
        with pytest.raises(ValueError, match=match):
            BloomFilter(n, p)


# ── No false negatives ───────────────────────────────────────────────────────

class TestNoFalseNegatives:
    def test_single(self):
        bf = BloomFilter(100, 0.01)
        bf.add("https://x.com")
        assert bf.contains("https://x.com")

    def test_bulk_1000(self):
        bf = BloomFilter(1000, 0.01)
        urls = [URL(i) for i in range(1000)]
        for u in urls: bf.add(u)
        assert all(bf.contains(u) for u in urls)

    def test_duplicate_add_safe(self):
        bf = BloomFilter(100, 0.01)
        bf.add("https://dup.com"); bf.add("https://dup.com")
        assert bf.contains("https://dup.com")

    def test_count_tracks_calls_not_unique(self):
        bf = BloomFilter(100, 0.01)
        bf.add("https://a.com"); bf.add("https://a.com"); bf.add("https://b.com")
        assert bf.item_count == 3

    def test_at_capacity(self):
        bf = BloomFilter(500, 0.01)
        urls = [URL(i) for i in range(500)]
        for u in urls: bf.add(u)
        assert all(bf.contains(u) for u in urls)


# ── FPR correctness ──────────────────────────────────────────────────────────

class TestFPR:
    EPSILON = 0.02
    N_PROBES = 10_000

    def _empirical(self, bf, n_insert, n_probe):
        for i in range(n_insert): bf.add(URL(i))
        return sum(bf.contains(PROBE(i)) for i in range(n_probe)) / n_probe

    @pytest.mark.parametrize("n,p", [
        (1_000, 0.01), (1_000, 0.05), (5_000, 0.01), (10_000, 0.001),
    ])
    def test_empirical_matches_theoretical(self, n, p):
        bf = BloomFilter(n, p)
        emp = self._empirical(bf, n, self.N_PROBES)
        theo = bf.false_positive_rate()
        assert abs(emp - theo) < self.EPSILON, (
            f"n={n},p={p}: empirical={emp:.4f} theoretical={theo:.4f} delta={abs(emp-theo):.4f}"
        )

    def test_empty_has_zero_fpr(self):
        bf = BloomFilter(1000, 0.01)
        assert bf.false_positive_rate() == 0.0

    def test_fpr_rises_with_fill(self):
        bf = BloomFilter(100, 0.01)
        prev = 0.0
        for i in range(500):
            bf.add(URL(i))
            cur = bf.false_positive_rate()
            assert cur >= prev - 1e-10
            prev = cur

    def test_overfilled_exceeds_target(self):
        bf = BloomFilter(100, 0.01)
        for i in range(1000): bf.add(URL(i))
        assert bf.false_positive_rate() > 0.01


# ── Edge cases ───────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_string(self):
        bf = BloomFilter(100, 0.01); bf.add(""); assert bf.contains("")

    def test_unicode_url(self):
        bf = BloomFilter(100, 0.01)
        u = "https://例え.jp/ページ/１"
        bf.add(u); assert bf.contains(u)

    def test_very_long_url(self):
        bf = BloomFilter(100, 0.01)
        u = "https://x.com/" + "a" * 2000
        bf.add(u); assert bf.contains(u)

    def test_positions_in_bounds(self):
        bf = BloomFilter(1000, 0.01)
        for item in ["https://x.com", "", "a" * 5000, "https://例え.jp"]:
            assert all(0 <= p < bf.bit_array_size for p in bf._positions(item))

    def test_empty_filter_no_false_positives(self):
        bf = BloomFilter(100, 0.01)
        assert not any(bf.contains(f"https://site-{i}.com") for i in range(1000))


# ── Diagnostics ──────────────────────────────────────────────────────────────

class TestDiagnostics:
    def test_memory_matches_bit_array(self):
        bf = BloomFilter(1000, 0.01)
        assert bf.memory_usage_bytes() == math.ceil(bf.bit_array_size / 8)

    def test_bloom_much_smaller_than_set(self):
        import sys
        n = 100_000
        bf = BloomFilter(n, 0.01)
        urls = [URL(i) for i in range(n)]
        for u in urls: bf.add(u)
        s = set(urls)
        set_mem = sys.getsizeof(s) + sum(sys.getsizeof(u) for u in list(s)[:1000]) * n // 1000
        assert bf.memory_usage_bytes() * 10 < set_mem
