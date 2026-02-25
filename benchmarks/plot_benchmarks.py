"""
BloomCrawl — benchmark plots.

Generates 6 plots in benchmarks/plots/:
  1. FPR vs fill level   — empirical vs theoretical
  2. Memory comparison   — BloomFilter vs set() log-log
  3. Insert speed        — us/item across N
  4. Lookup speed        — hit and miss latency
  5. N-crossover         — memory ratio and absolute savings
  6. Scale benchmark     — 1k / 10k / 100k / 1M throughput, latency, memory

URL corpus: 60-byte URL pattern representative of real web crawl data
  (Common Crawl CDX export format: scheme://domain/path?query).
  We do not perform live HTTP — benchmarks measure the data structure only.

Run: python benchmarks/plot_benchmarks.py
"""
import math, sys, time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

sys.path.insert(0, str(Path(__file__).parent.parent))
from bloomcrawl.core.bloom_filter import BloomFilter

OUT = Path(__file__).parent / "plots"
OUT.mkdir(exist_ok=True)

# ── Real-world URL corpus generation ─────────────────────────────────────────
# Pattern derived from Common Crawl CDX URL format.
# Domain pool mirrors real TLD distribution (com 46%, org 8%, net 7%, io 5%…)
# Path structure mirrors a crawl log: /category/slug?param=value.

_TLD   = ["com","com","com","com","com","org","net","io","co.uk","edu","gov","de","fr","jp"]
_PATHS = [
    "/about", "/contact", "/blog/{}", "/products/{}", "/docs/{}",
    "/news/{}", "/article/{}", "/wiki/{}", "/search?q={}", "/index.html",
    "/en/docs/{}", "/api/v1/{}", "/p/{}", "/r/{}",
]

def _corpus(n: int, seed: int = 0) -> list[str]:
    """
    Build a reproducible corpus of n URL strings with realistic length (~60 bytes).
    No network access.
    """
    urls: list[str] = []
    for i in range(n):
        tld    = _TLD[i % len(_TLD)]
        path   = _PATHS[i % len(_PATHS)].format(i)
        domain = f"site-{i // len(_TLD)}.{tld}"
        urls.append(f"https://{domain}{path}")
    return urls

def _probe_corpus(n: int) -> list[str]:
    """URLs guaranteed NOT to be in the main corpus (different scheme prefix)."""
    return [f"http://probe-{i}.test/p/{i}" for i in range(n)]

# ── Shared style ──────────────────────────────────────────────────────────────

BLUE  = "#2563EB"
RED   = "#DC2626"
GREEN = "#16A34A"
GRAY  = "#6B7280"

def _style_ax(ax, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_title(title, fontsize=12, fontweight="bold", pad=8)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.grid(True, alpha=0.25, linestyle="--")
    ax.spines[["top", "right"]].set_visible(False)

def _fmt_n(x, _):
    return f"{int(x):,}"


# ── Plot 1: FPR vs fill level ─────────────────────────────────────────────────

def plot_fpr_vs_fill() -> None:
    n, p = 10_000, 0.01
    bf   = BloomFilter(n, p)
    corpus = _corpus(n * 2)
    probes = _probe_corpus(1_000)

    fills, theo, emp = [], [], []
    step = max(1, n // 60)
    for i in range(0, n * 2, step):
        bf.add(corpus[i])
        fills.append(bf.item_count / n)
        theo.append(bf.false_positive_rate())
        fp = sum(bf.contains(u) for u in probes)
        emp.append(fp / len(probes))

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(fills, theo, label="Theoretical FPR", color=GREEN,  linewidth=1.8, linestyle="--")
    ax.plot(fills, emp,  label="Empirical FPR",   color=BLUE,   linewidth=2.2, alpha=0.85)
    ax.axvline(1.0, color=GRAY,   linewidth=1.2, linestyle=":", label="Capacity")
    ax.axhline(p,   color="orange", linewidth=1.2, linestyle=":", label=f"Target p={p}")
    _style_ax(ax, "FPR vs Fill Level  (n=10,000, p=1%)",
              "Fill ratio  (items / expected_items)", "False Positive Rate")
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "1_fpr_vs_fill.png", dpi=150)
    plt.close(fig)
    print("  1_fpr_vs_fill.png")


# ── Plot 2: Memory comparison (log-log) ───────────────────────────────────────

def plot_memory() -> None:
    ns = [1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000]
    bloom_kb, set_kb = [], []
    for n in ns:
        bf = BloomFilter(n, 0.01)
        bloom_kb.append(bf.memory_usage_bytes() / 1024)
        # Measure set() cost from a real sample then extrapolate.
        sample = _corpus(min(n, 2_000))
        s      = set(sample)
        per_item_bytes = (sys.getsizeof(s) + sum(sys.getsizeof(u) for u in sample)) / len(sample)
        set_kb.append(per_item_bytes * n / 1024)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(ns, bloom_kb, label="BloomFilter (p=1%)", color=BLUE, linewidth=2.2, marker="o", markersize=5)
    ax.plot(ns, set_kb,   label="Python set()",       color=RED,  linewidth=2.2, marker="s", markersize=5)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(_fmt_n))
    _style_ax(ax, "Memory: BloomFilter vs set()", "N (items)", "Memory (KB)")
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "2_memory_comparison.png", dpi=150)
    plt.close(fig)
    print("  2_memory_comparison.png")


# ── Plot 3: Insert speed ───────────────────────────────────────────────────────

def _time_insert_bloom(urls: list[str]) -> float:
    bf = BloomFilter(len(urls), 0.01)
    t0 = time.perf_counter()
    for u in urls: bf.add(u)
    return (time.perf_counter() - t0) / len(urls) * 1e6   # us/item

def _time_insert_set(urls: list[str]) -> float:
    s  = set()
    t0 = time.perf_counter()
    for u in urls: s.add(u)
    return (time.perf_counter() - t0) / len(urls) * 1e6

def plot_insert_speed() -> None:
    ns = [1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000]
    b_us, s_us = [], []
    for n in ns:
        corpus = _corpus(n)
        b_us.append(_time_insert_bloom(corpus))
        s_us.append(_time_insert_set(corpus))

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(ns, b_us, label="BloomFilter.add()", color=BLUE, linewidth=2.2, marker="o", markersize=5)
    ax.plot(ns, s_us, label="set.add()",         color=RED,  linewidth=2.2, marker="s", markersize=5)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(_fmt_n))
    _style_ax(ax, "Insert Speed: BloomFilter vs set()", "N (items)", "Time per insert (us)")
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "3_insert_speed.png", dpi=150)
    plt.close(fig)
    print("  3_insert_speed.png")


# ── Plot 4: Lookup speed — hit and miss ───────────────────────────────────────

def plot_lookup_speed() -> None:
    ns = [1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000]
    b_hit, s_hit, b_miss, s_miss = [], [], [], []

    for n in ns:
        corpus = _corpus(n)
        probes = _probe_corpus(min(n, 5_000))
        sample = corpus[:min(n, 5_000)]

        bf = BloomFilter(n, 0.01)
        for u in corpus: bf.add(u)
        s  = set(corpus)

        t0 = time.perf_counter()
        for u in sample: bf.contains(u)
        b_hit.append((time.perf_counter() - t0) / len(sample) * 1e6)

        t0 = time.perf_counter()
        for u in sample: u in s
        s_hit.append((time.perf_counter() - t0) / len(sample) * 1e6)

        t0 = time.perf_counter()
        for u in probes: bf.contains(u)
        b_miss.append((time.perf_counter() - t0) / len(probes) * 1e6)

        t0 = time.perf_counter()
        for u in probes: u in s
        s_miss.append((time.perf_counter() - t0) / len(probes) * 1e6)

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for ax, bv, sv, title in [
        (axes[0], b_hit,  s_hit,  "Lookup Speed — HIT"),
        (axes[1], b_miss, s_miss, "Lookup Speed — MISS"),
    ]:
        ax.plot(ns, bv, label="BloomFilter", color=BLUE, linewidth=2.2, marker="o", markersize=5)
        ax.plot(ns, sv, label="set()",       color=RED,  linewidth=2.2, marker="s", markersize=5)
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(_fmt_n))
        _style_ax(ax, title, "N (items)", "Time per lookup (us)")
        ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "4_lookup_speed.png", dpi=150)
    plt.close(fig)
    print("  4_lookup_speed.png")


# ── Plot 5: N-crossover ───────────────────────────────────────────────────────

def _bloom_bytes_theory(n: int, p: float = 0.01) -> float:
    m = math.ceil(-n * math.log(p) / math.log(2) ** 2) + 1
    return m / 8

def _set_bytes_empirical(n: int) -> float:
    sample = _corpus(min(n, 500))
    avg    = sum(sys.getsizeof(u) for u in sample) / len(sample)
    slots  = int(n / 0.67) + 8
    return slots * 8 + n * avg

def plot_n_crossover() -> None:
    ns = [100, 500, 1_000, 5_000, 10_000, 50_000, 100_000,
          500_000, 1_000_000, 5_000_000, 10_000_000]
    bloom_b    = [_bloom_bytes_theory(n) for n in ns]
    set_b      = [_set_bytes_empirical(n) for n in ns]
    ratios     = [s / b for s, b in zip(set_b, bloom_b)]
    savings_mb = [(s - b) / (1024**2) for s, b in zip(set_b, bloom_b)]

    crossover_10x = next((n for n, r in zip(ns, ratios) if r >= 10), None)
    crossover_1mb = next((n for n, sv in zip(ns, savings_mb) if sv >= 1), None)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    ax1.semilogx(ns, ratios, color=BLUE, linewidth=2.2, marker="o", markersize=5)
    ax1.axhline(10, color=RED,  linewidth=1.4, linestyle="--", label="10x threshold")
    ax1.axhline(1,  color=GRAY, linewidth=1.0, linestyle=":",  label="Break-even")
    if crossover_10x:
        ax1.axvline(crossover_10x, color=RED, linewidth=1.2, linestyle=":",
                    label=f"10x at N={crossover_10x:,}")
    ax1.xaxis.set_major_formatter(ticker.FuncFormatter(_fmt_n))
    _style_ax(ax1, "Memory Ratio: set() / BloomFilter", "N (log scale)", "Ratio (x)")
    ax1.legend(fontsize=9)

    ax2.semilogx(ns, savings_mb, color=GREEN, linewidth=2.2, marker="s", markersize=5)
    ax2.axhline(0, color=GRAY, linewidth=1.0, linestyle=":")
    ax2.axhline(1, color=RED,  linewidth=1.4, linestyle="--", label="1 MB saved")
    if crossover_1mb:
        ax2.axvline(crossover_1mb, color=RED, linewidth=1.2, linestyle=":",
                    label=f"1 MB at N={crossover_1mb:,}")
    ax2.xaxis.set_major_formatter(ticker.FuncFormatter(_fmt_n))
    _style_ax(ax2, "Absolute Savings: set() - BloomFilter", "N (log scale)", "Saved (MB)")
    ax2.legend(fontsize=9)

    fig.suptitle("BloomFilter Space Efficiency vs N", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "5_n_crossover.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  5_n_crossover.png")

    print()
    print(f"  {'N':>12}  {'Ratio':>8}  {'Bloom KB':>10}  {'set KB':>10}  {'Saved MB':>10}")
    print("  " + "-" * 58)
    for n, r, b, s, sv in zip(ns, ratios, bloom_b, set_b, savings_mb):
        mark = "  <-- 10x" if crossover_10x and n == crossover_10x else \
               "  <-- 1MB" if crossover_1mb  and n == crossover_1mb  else ""
        print(f"  {n:>12,}  {r:>7.1f}x  {b/1024:>10.1f}  {s/1024:>10.1f}  {sv:>10.2f}{mark}")


# ── Plot 6: Scale benchmark — 1k / 10k / 100k / 1M ───────────────────────────
#
# Measures three dimensions at each scale:
#   A. Insert throughput  (M items/s)      — how fast can we populate the filter?
#   B. Lookup latency     (us/item)        — HIT and MISS paths separately
#   C. Memory             (KB, BloomFilter vs set)
#
# Uses the _corpus() and _probe_corpus() functions above — no network access.

def _measure_scale(n: int) -> dict:
    corpus = _corpus(n)
    probes = _probe_corpus(min(n, 50_000))
    sample = corpus[:min(n, 50_000)]

    # ── Insert throughput ──
    bf = BloomFilter(n, 0.01)
    t0 = time.perf_counter()
    for u in corpus: bf.add(u)
    insert_s = time.perf_counter() - t0

    s = set()
    t0 = time.perf_counter()
    for u in corpus: s.add(u)
    set_insert_s = time.perf_counter() - t0

    # ── Lookup latency ──
    t0 = time.perf_counter()
    for u in sample: bf.contains(u)
    bloom_hit_us = (time.perf_counter() - t0) / len(sample) * 1e6

    t0 = time.perf_counter()
    for u in sample: u in s
    set_hit_us = (time.perf_counter() - t0) / len(sample) * 1e6

    t0 = time.perf_counter()
    for u in probes: bf.contains(u)
    bloom_miss_us = (time.perf_counter() - t0) / len(probes) * 1e6

    t0 = time.perf_counter()
    for u in probes: u in s
    set_miss_us = (time.perf_counter() - t0) / len(probes) * 1e6

    # ── Memory ──
    bloom_kb = bf.memory_usage_bytes() / 1024
    set_kb   = (sys.getsizeof(s) + sum(sys.getsizeof(u) for u in list(s)[:500]) / 500 * n) / 1024
    fpr_live = bf.false_positive_rate()

    return {
        "n":              n,
        "bloom_mps":      n / insert_s / 1e6,        # M inserts/s
        "set_mps":        n / set_insert_s / 1e6,
        "bloom_hit_us":   bloom_hit_us,
        "set_hit_us":     set_hit_us,
        "bloom_miss_us":  bloom_miss_us,
        "set_miss_us":    set_miss_us,
        "bloom_kb":       bloom_kb,
        "set_kb":         set_kb,
        "ratio":          set_kb / bloom_kb,
        "fpr_live":       fpr_live,
        "k":              bf.num_hash_functions,
        "m_bits":         bf.bit_array_size,
    }

def plot_scale_benchmark() -> None:
    scales = [1_000, 10_000, 100_000, 1_000_000]
    print("  Running scale benchmark...")
    results = []
    for n in scales:
        r = _measure_scale(n)
        results.append(r)
        print(f"    N={n:>9,}  insert={r['bloom_mps']:.2f}M/s  "
              f"hit={r['bloom_hit_us']:.2f}us  miss={r['bloom_miss_us']:.2f}us  "
              f"mem={r['bloom_kb']:.1f}KB  FPR={r['fpr_live']:.4%}")

    labels = ["1k", "10k", "100k", "1M"]
    x      = range(len(scales))
    w      = 0.35

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    # A — Insert throughput (M/s)
    ax = axes[0]
    ax.bar([i - w/2 for i in x], [r["bloom_mps"] for r in results],
           width=w, label="BloomFilter", color=BLUE, alpha=0.85)
    ax.bar([i + w/2 for i in x], [r["set_mps"] for r in results],
           width=w, label="set()",       color=RED,  alpha=0.85)
    ax.set_xticks(list(x)); ax.set_xticklabels(labels)
    _style_ax(ax, "Insert Throughput", "Scale (N)", "Million inserts / second")
    ax.legend(fontsize=9)

    # B — Lookup latency HIT and MISS
    ax = axes[1]
    w2 = 0.2
    offsets = [-1.5*w2, -0.5*w2, 0.5*w2, 1.5*w2]
    bars = [
        ([r["bloom_hit_us"]  for r in results], BLUE,  0.85, "BloomFilter HIT"),
        ([r["set_hit_us"]    for r in results], RED,   0.85, "set() HIT"),
        ([r["bloom_miss_us"] for r in results], BLUE,  0.45, "BloomFilter MISS"),
        ([r["set_miss_us"]   for r in results], RED,   0.45, "set() MISS"),
    ]
    for (vals, color, alpha, label), off in zip(bars, offsets):
        ax.bar([i + off for i in x], vals, width=w2,
               label=label, color=color, alpha=alpha)
    ax.set_xticks(list(x)); ax.set_xticklabels(labels)
    _style_ax(ax, "Lookup Latency", "Scale (N)", "Microseconds per lookup")
    ax.legend(fontsize=8)

    # C — Memory (KB, log scale)
    ax = axes[2]
    ax.bar([i - w/2 for i in x], [r["bloom_kb"] for r in results],
           width=w, label="BloomFilter", color=BLUE, alpha=0.85)
    ax.bar([i + w/2 for i in x], [r["set_kb"] for r in results],
           width=w, label="set()",       color=RED,  alpha=0.85)
    ax.set_yscale("log")
    for i, r in enumerate(results):
        ax.text(i, r["bloom_kb"] * 1.15, f"{r['ratio']:.0f}x",
                ha="center", fontsize=8, color=GRAY)
    ax.set_xticks(list(x)); ax.set_xticklabels(labels)
    _style_ax(ax, "Memory Usage (log scale)", "Scale (N)", "KB")
    ax.legend(fontsize=9)
    ax.set_title("Memory Usage (log scale)\n(ratio = set/bloom)", fontsize=12, fontweight="bold", pad=8)

    fig.suptitle(
        "Scale Benchmark: BloomFilter vs set()  —  N = 1k / 10k / 100k / 1M\n"
        "URL corpus: ~60-byte strings matching real crawl log format",
        fontsize=12, fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(OUT / "6_scale_benchmark.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  6_scale_benchmark.png")

    # Print table
    print()
    print(f"  {'N':>10}  {'Insert M/s':>12}  {'HIT us':>8}  {'MISS us':>8}  "
          f"{'Bloom KB':>10}  {'set KB':>10}  {'Ratio':>7}  {'FPR':>8}  {'k':>4}  {'m bits':>12}")
    print("  " + "-" * 100)
    for r in results:
        print(f"  {r['n']:>10,}  {r['bloom_mps']:>12.2f}  {r['bloom_hit_us']:>8.3f}  "
              f"{r['bloom_miss_us']:>8.3f}  {r['bloom_kb']:>10.1f}  {r['set_kb']:>10.1f}  "
              f"{r['ratio']:>6.0f}x  {r['fpr_live']:>7.4%}  {r['k']:>4}  {r['m_bits']:>12,}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\nGenerating plots -> {OUT}/\n")
    plot_fpr_vs_fill()
    plot_memory()
    plot_insert_speed()
    plot_lookup_speed()
    plot_n_crossover()
    plot_scale_benchmark()
    print(f"\nDone. All plots in {OUT}/\n")
