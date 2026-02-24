"""
Benchmarks: BloomFilter vs Python set().
Run: pytest benchmarks/bench_bloom_filter.py --benchmark-only -v
"""
import sys
import pytest
from bloomcrawl.core.bloom_filter import BloomFilter

N   = 100_000
FPR = 0.01
URLS   = [f"https://test-{i}.example.com/page/{i}" for i in range(N)]
PROBES = [f"https://probe-{i}.example.com/p/{i}"   for i in range(10_000)]


@pytest.fixture(scope="module")
def bloom():
    bf = BloomFilter(N, FPR)
    for u in URLS: bf.add(u)
    return bf


@pytest.fixture(scope="module")
def seen_set():
    return set(URLS)


# ── Insert ───────────────────────────────────────────────────────────────────

def test_bloom_insert(benchmark):
    def run():
        bf = BloomFilter(N, FPR)
        for u in URLS: bf.add(u)
    benchmark(run)


def test_set_insert(benchmark):
    def run():
        s = set()
        for u in URLS: s.add(u)
    benchmark(run)


# ── Lookup hit (item in structure) ───────────────────────────────────────────

def test_bloom_lookup_hit(benchmark, bloom):
    benchmark(lambda: [bloom.contains(u) for u in URLS[:10_000]])


def test_set_lookup_hit(benchmark, seen_set):
    benchmark(lambda: [u in seen_set for u in URLS[:10_000]])


# ── Lookup miss (item not in structure) ──────────────────────────────────────

def test_bloom_lookup_miss(benchmark, bloom):
    benchmark(lambda: [bloom.contains(u) for u in PROBES])


def test_set_lookup_miss(benchmark, seen_set):
    benchmark(lambda: [u in seen_set for u in PROBES])


# ── Memory comparison ─────────────────────────────────────────────────────────

def test_memory_comparison(bloom, seen_set, capsys):
    bloom_b = bloom.memory_usage_bytes()
    set_b   = sys.getsizeof(seen_set) + int(
        sum(sys.getsizeof(u) for u in list(seen_set)[:1000]) / 1000 * N
    )
    with capsys.disabled():
        print(f"\n{'─'*52}")
        print(f"  Memory · N={N:,} URLs")
        print(f"{'─'*52}")
        print(f"  BloomFilter : {bloom_b:>10,} bytes  ({bloom_b/1024:.1f} KB)")
        print(f"  set()       : {set_b:>10,} bytes  ({set_b/1024/1024:.1f} MB)")
        print(f"  Ratio       : {set_b/bloom_b:.0f}× smaller with BloomFilter")
        print(f"  FPR @ cap   : {bloom.false_positive_rate():.4%}")
        print(f"  m={bloom.bit_array_size:,} bits  k={bloom.num_hash_functions}")
        print(f"{'─'*52}\n")
    assert bloom_b * 20 < set_b
