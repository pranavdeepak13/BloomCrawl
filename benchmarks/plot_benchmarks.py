"""
BloomCrawl — benchmark plots.

Generates 5 plots in benchmarks/plots/:
1. FPR vs fill level (empirical vs theoretical)
2. Memory: BloomFilter vs set() across N
3. Insert speed: BloomFilter vs set() across N
4. Lookup speed: BloomFilter vs set() (hit + miss)
5. N-crossover: memory ratio & savings vs N (where does BloomFilter win?)

Run: python benchmarks/plot_benchmarks.py
"""
import math, sys, time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

sys.path.insert(0, str(Path(__file__).parent.parent))
from bloomcrawl.core.bloom_filter import BloomFilter

OUT = Path(__file__).parent / "plots"
OUT.mkdir(exist_ok=True)

STYLE = {
    "bloom": {"color": "#2563EB", "linewidth": 2.2, "marker": "o", "markersize": 5},
    "set":   {"color": "#DC2626", "linewidth": 2.2, "marker": "s", "markersize": 5},
    "theo":  {"color": "#16A34A", "linewidth": 1.8, "linestyle": "--"},
    "emp":   {"color": "#2563EB", "linewidth": 2.2},
}

def _ax(ax, title, xlabel, ylabel):
    ax.set_title(title, fontsize=13, fontweight="bold", pad=10)
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(True, alpha=0.3, linestyle="--")
    ax.spines[["top", "right"]].set_visible(False)


# ── Plot 1: FPR vs fill level ────────────────────────────────────────────────

def plot_fpr_vs_fill():
    fig, ax = plt.subplots(figsize=(7, 4.5))
    n, p = 1_000, 0.01
    bf = BloomFilter(n, p)
    url = lambda i: f"https://t-{i}.com"
    probe = lambda i: f"https://p-{i}.com"

    fills, theo_rates, emp_rates = [], [], []
    step = max(1, n // 50)
    for i in range(0, n * 2, step):
        bf.add(url(i))
        fills.append(bf.item_count / n)
        theo_rates.append(bf.false_positive_rate())
        fp = sum(bf.contains(probe(j)) for j in range(500))
        emp_rates.append(fp / 500)

    ax.plot(fills, theo_rates, label="Theoretical FPR", **STYLE["theo"])
    ax.plot(fills, emp_rates,  label="Empirical FPR",   **STYLE["emp"], alpha=0.85)
    ax.axvline(1.0, color="gray", linewidth=1.2, linestyle=":", label="Capacity (n=1000)")
    ax.axhline(p,   color="orange", linewidth=1.2, linestyle=":", label=f"Target p={p}")
    _ax(ax, "FPR vs Fill Level", "Fill ratio (items inserted / expected_items)", "False Positive Rate")
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "1_fpr_vs_fill.png", dpi=150)
    plt.close(fig)
    print("  ✓ 1_fpr_vs_fill.png")


# ── Plot 2: Memory comparison ────────────────────────────────────────────────

def plot_memory():
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ns = [1_000, 5_000, 10_000, 50_000, 100_000, 500_000]
    bloom_kb, set_kb = [], []
    for n in ns:
        bf = BloomFilter(n, 0.01)
        bloom_kb.append(bf.memory_usage_bytes() / 1024)
        urls = [f"https://t-{i}.com" for i in range(min(n, 10_000))]
        s = set(urls)
        est = (sys.getsizeof(s) + sum(sys.getsizeof(u) for u in urls)) * (n / len(urls)) / 1024
        set_kb.append(est)

    ax.plot(ns, bloom_kb, label="BloomFilter", **STYLE["bloom"])
    ax.plot(ns, set_kb,   label="set()",       **STYLE["set"])
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):,}"))
    _ax(ax, "Memory: BloomFilter vs set()", "Number of items (N)", "Memory (KB)")
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "2_memory_comparison.png", dpi=150)
    plt.close(fig)
    print("  ✓ 2_memory_comparison.png")


# ── Plot 3: Insert speed ─────────────────────────────────────────────────────

def _time_insert_bloom(n):
    bf = BloomFilter(n, 0.01)
    urls = [f"https://t-{i}.com" for i in range(n)]
    t0 = time.perf_counter()
    for u in urls: bf.add(u)
    return (time.perf_counter() - t0) / n * 1e6  # µs per insert


def _time_insert_set(n):
    urls = [f"https://t-{i}.com" for i in range(n)]
    t0 = time.perf_counter()
    s = set()
    for u in urls: s.add(u)
    return (time.perf_counter() - t0) / n * 1e6


def plot_insert_speed():
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ns = [1_000, 5_000, 10_000, 50_000, 100_000]
    b_us = [_time_insert_bloom(n) for n in ns]
    s_us = [_time_insert_set(n)   for n in ns]
    ax.plot(ns, b_us, label="BloomFilter.add()", **STYLE["bloom"])
    ax.plot(ns, s_us, label="set.add()",         **STYLE["set"])
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):,}"))
    _ax(ax, "Insert Speed: BloomFilter vs set()", "Number of items (N)", "Time per insert (µs)")
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "3_insert_speed.png", dpi=150)
    plt.close(fig)
    print("  ✓ 3_insert_speed.png")


# ── Plot 4: Lookup speed (hit + miss) ────────────────────────────────────────

def plot_lookup_speed():
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    ns = [1_000, 5_000, 10_000, 50_000, 100_000]

    bloom_hit, set_hit, bloom_miss, set_miss = [], [], [], []
    for n in ns:
        urls   = [f"https://t-{i}.com"   for i in range(n)]
        probes = [f"https://p-{i}.com"   for i in range(min(n, 5_000))]
        bf = BloomFilter(n, 0.01)
        for u in urls: bf.add(u)
        s = set(urls)

        sample = urls[:min(n, 5_000)]
        t0 = time.perf_counter()
        for u in sample: bf.contains(u)
        bloom_hit.append((time.perf_counter() - t0) / len(sample) * 1e6)

        t0 = time.perf_counter()
        for u in sample: u in s
        set_hit.append((time.perf_counter() - t0) / len(sample) * 1e6)

        t0 = time.perf_counter()
        for u in probes: bf.contains(u)
        bloom_miss.append((time.perf_counter() - t0) / len(probes) * 1e6)

        t0 = time.perf_counter()
        for u in probes: u in s
        set_miss.append((time.perf_counter() - t0) / len(probes) * 1e6)

    for ax, b, sv, title in [
        (axes[0], bloom_hit,  set_hit,  "Lookup Speed — HIT"),
        (axes[1], bloom_miss, set_miss, "Lookup Speed — MISS"),
    ]:
        ax.plot(ns, b,  label="BloomFilter", **STYLE["bloom"])
        ax.plot(ns, sv, label="set()",       **STYLE["set"])
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):,}"))
        _ax(ax, title, "Number of items (N)", "Time per lookup (µs)")
        ax.legend(fontsize=9)

    fig.tight_layout()
    fig.savefig(OUT / "4_lookup_speed.png", dpi=150)
    plt.close(fig)
    print("  ✓ 4_lookup_speed.png")


# ── Plot 5: N-crossover analysis ─────────────────────────────────────────────

def _bloom_bytes(n: int, p: float = 0.01) -> float:
    """Theoretical Bloom Filter memory in bytes for n items at FPR p."""
    m = math.ceil(-n * math.log(p) / math.log(2) ** 2) + 1
    return m / 8  # bits → bytes


def _set_bytes(n: int) -> float:
    """
    Empirical Python set memory estimate for n URL strings.
    Samples 500 URLs to get average string size, then scales.
    Formula: sys.getsizeof(set) grows in steps (load factor ~⅔),
             plus per-element: pointer (8B) + str object (~57B + len(s)).
    """
    sample_n = min(n, 500)
    sample_urls = [f"https://example-domain-{i}.com/path/to/page" for i in range(sample_n)]
    avg_str_bytes = sum(sys.getsizeof(u) for u in sample_urls) / sample_n
    # set overhead: ~200B base + 8 bytes per slot (pointer), load factor ~2/3
    set_slots = int(n / 0.67) + 8   # next power of 2 ≥ n/0.67
    set_overhead = set_slots * 8
    str_total = n * avg_str_bytes
    return set_overhead + str_total


def plot_n_crossover():
    # Wide range: from tiny (10) to very large (10M)
    ns = [10, 50, 100, 500, 1_000, 5_000, 10_000,
          50_000, 100_000, 500_000, 1_000_000, 5_000_000, 10_000_000]

    bloom_b = [_bloom_bytes(n) for n in ns]
    set_b   = [_set_bytes(n)   for n in ns]
    ratios  = [s / b for s, b in zip(set_b, bloom_b)]
    savings_mb = [(s - b) / (1024 ** 2) for s, b in zip(set_b, bloom_b)]

    # Find crossover: first N where ratio >= 10 (10× savings)
    crossover_10x = next((n for n, r in zip(ns, ratios) if r >= 10), None)
    # Find first N where absolute savings >= 1 MB
    crossover_1mb = next((n for n, s in zip(ns, savings_mb) if s >= 1), None)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))

    # Left: memory ratio (set / bloom)
    ax1.semilogx(ns, ratios, color="#2563EB", linewidth=2.5, marker="o", markersize=5)
    ax1.axhline(10, color="#DC2626", linewidth=1.5, linestyle="--", label="10× savings threshold")
    ax1.axhline(1,  color="gray",    linewidth=1.0, linestyle=":",  label="Break-even (1×)")
    if crossover_10x:
        ax1.axvline(crossover_10x, color="#DC2626", linewidth=1.2, linestyle=":",
                    label=f"10× crossover ≈ N={crossover_10x:,}")
    ax1.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):,}"))
    _ax(ax1,
        "Memory Ratio: set() / BloomFilter",
        "Number of items N (log scale)",
        "Memory ratio (×)")
    ax1.legend(fontsize=9)

    # Right: absolute savings in MB
    ax2.semilogx(ns, savings_mb, color="#16A34A", linewidth=2.5, marker="s", markersize=5)
    ax2.axhline(0, color="gray",    linewidth=1.0, linestyle=":")
    ax2.axhline(1, color="#DC2626", linewidth=1.5, linestyle="--", label="1 MB saved")
    if crossover_1mb:
        ax2.axvline(crossover_1mb, color="#DC2626", linewidth=1.2, linestyle=":",
                    label=f"1 MB crossover ≈ N={crossover_1mb:,}")
    ax2.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):,}"))
    _ax(ax2,
        "Absolute Memory Saved: set() − BloomFilter",
        "Number of items N (log scale)",
        "Memory saved (MB)")
    ax2.legend(fontsize=9)

    # Annotation box with key findings
    findings = []
    for n, r in zip(ns, ratios):
        if r >= 2:
            findings.append(f"  N={n:>10,}  →  {r:5.1f}× smaller  ({_bloom_bytes(n)/1024:.1f} KB vs {_set_bytes(n)/1024:.1f} KB)")
    note = "Key crossover points:\n" + "\n".join(findings[:6])
    fig.text(0.5, -0.04, note, ha="center", fontsize=8,
             family="monospace", color="#374151",
             bbox=dict(boxstyle="round,pad=0.5", facecolor="#F3F4F6", edgecolor="#D1D5DB"))

    fig.suptitle("BloomFilter Space Efficiency: When Does It Matter?",
                 fontsize=14, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(OUT / "5_n_crossover.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    # Print crossover summary to terminal
    print("  ✓ 5_n_crossover.png")
    print()
    print("  N-Crossover Summary (p=1%, typical URL string ~50 bytes):")
    print(f"  {'N':>12}  {'Ratio':>8}  {'Bloom (KB)':>12}  {'set (KB)':>12}  {'Saved (MB)':>12}")
    print("  " + "-" * 62)
    for n, r, b, s, sv in zip(ns, ratios, bloom_b, set_b, savings_mb):
        marker = " ◄ 10×" if crossover_10x and n == crossover_10x else ""
        marker = " ◄ 1MB" if crossover_1mb and n == crossover_1mb else marker
        print(f"  {n:>12,}  {r:>8.1f}×  {b/1024:>12.1f}  {s/1024:>12.1f}  {sv:>12.2f}{marker}")
    print()
    if crossover_10x:
        print(f"  → BloomFilter is 10× more memory-efficient starting at N ≈ {crossover_10x:,}")
    if crossover_1mb:
        print(f"  → Saves ≥1 MB of RAM starting at N ≈ {crossover_1mb:,}")


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\nGenerating plots → {OUT}/\n")
    plot_fpr_vs_fill()
    plot_memory()
    plot_insert_speed()
    plot_lookup_speed()
    plot_n_crossover()
    print(f"\nAll plots saved to {OUT}/\n")
