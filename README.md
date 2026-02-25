# BloomCrawl

A from-scratch web crawler and search system built to understand two things:
1. **How to design a scalable distributed system** (modelled on the system-design-primer).
2. **How Bloom Filters work** — implemented from scratch, tested empirically, and benchmarked against Python `set()`.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [System Architecture](#system-architecture)
3. [Component Guide](#component-guide)
4. [Key Design Decisions](#key-design-decisions)
5. [Bloom Filter Deep Dive](#bloom-filter-deep-dive)
6. [Akamai Caching Pattern](#akamai-caching-pattern)
7. [Running the Project](#running-the-project)
8. [Benchmark Results](#benchmark-results)

---

## Project Structure

```
BloomCrawl/
├── bloomcrawl/
│   ├── core/
│   │   ├── bloom_filter.py        # BloomFilter — bitarray + MurmurHash3
│   │   ├── simhash.py             # 64-bit Simhash for near-duplicate detection
│   │   └── dedup_job.py           # Offline batch deduplication (MapReduce analogy)
│   ├── crawler/
│   │   ├── page.py                # Page dataclass (id, url, content, signature)
│   │   ├── url_frontier.py        # Priority queue of URLs to crawl
│   │   ├── page_store.py          # NoSQL-style store (in-memory, thread-safe)
│   │   └── crawler.py             # Crawl loop orchestrator
│   ├── workers/
│   │   ├── reverse_index.py       # Inverted index: word → [(page_id, score)]
│   │   ├── reverse_index_worker.py# Daemon thread consuming reverse_index_queue
│   │   └── doc_index_worker.py    # Daemon thread consuming doc_index_queue
│   └── frontend/
│       ├── query_parser.py        # 4-step pipeline: typed Result/ParseError
│       └── cache_manager.py       # Akamai two-tier cache (BloomFilter + Redis)
├── tests/
│   ├── test_bloom_filter.py       # 31 tests — construction, no-FN, empirical FPR
│   ├── test_simhash.py            # 11 tests — Hamming distance, near-dup detection
│   ├── test_url_frontier.py       # 7 tests  — priority ordering, thread safety
│   ├── test_crawler.py            # 6 tests  — cycle detection, termination
│   ├── test_query_parser.py       # 15 tests — per-step, adversarial inputs
│   └── test_cache_manager.py      # 8 tests  — Akamai pattern correctness
└── benchmarks/
    ├── bench_bloom_filter.py      # pytest-benchmark: insert, lookup, memory
    ├── plot_benchmarks.py         # Generates 4 matplotlib plots
    └── plots/
        ├── 1_fpr_vs_fill.png      # FPR vs fill level (empirical vs theoretical)
        ├── 2_memory_comparison.png# BloomFilter vs set() memory across N
        ├── 3_insert_speed.png     # Insert µs/op across N
        └── 4_lookup_speed.png     # Lookup µs/op (hit + miss) across N
```

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        FRONTEND (Search)                            │
│                                                                     │
│  User Query                                                         │
│      │                                                              │
│      ▼                                                              │
│  QueryParser (4-step pipeline)                                      │
│      │  validate → strip_markup → tokenise → normalise              │
│      │  returns ParsedQuery or ParseError (never silenced)          │
│      ▼                                                              │
│  CacheManager ──────── BloomFilter (request-frequency)              │
│      │                  "definitely no"  → skip Redis (1st req)     │
│      │                  "probably yes"   → check Redis              │
│      │                                                              │
│  ReverseIndex.lookup_multi(terms)  →  rank  →  results              │
└─────────────────────────────────────────────────────────────────────┘
                                  ▲
                           index built by
                                  │
┌─────────────────────────────────────────────────────────────────────┐
│                        CRAWLER SUBSYSTEM                            │
│                                                                     │
│  Seed URLs                                                          │
│      │                                                              │
│      ▼                                                              │
│  URLFrontier (PriorityQueue, thread-safe)                           │
│      │                                                              │
│      ▼                                                              │
│  Crawler.crawl()                                                    │
│    1. BloomFilter.contains(url)?  → skip if probably seen           │
│    2. fetch(url)                  → title, content, child_urls      │
│    3. PageStore.crawled_similar() → skip near-duplicate content     │
│    4. BloomFilter.add(url)        → mark seen                       │
│    5. PageStore.insert()          → persist page                    │
│    6. enqueue child_urls          → URLFrontier                     │
│    7. publish msg                 → reverse_index_queue             │
│                                   → doc_index_queue                 │
│                                                                     │
│  BloomFilter (URL dedup)    PageStore (NoSQL, RLock)                │
│  URLFrontier (priority BFS) DedupJob (offline batch)                │
└─────────────────────────────────────────────────────────────────────┘
          │                           │
          ▼                           ▼
  ReverseIndexWorker           DocIndexWorker
  (daemon thread)              (daemon thread)
          │
          ▼
  ReverseIndex  →  word → [(page_id, score)]
```

---

## Component Guide

### `bloomcrawl/core/bloom_filter.py` — BloomFilter

The core data structure of the project.

**What it does:** Answers "have I seen this URL before?" in O(k) time using ~8× less memory than a Python `set`.

**Key methods:**

| Method | Description |
|---|---|
| `add(item)` | Hash item to k positions, set those bits to 1 |
| `contains(item)` | Returns False → definitely new. True → probably seen |
| `false_positive_rate()` | Theoretical FPR at current fill: `(1 − e^(−k·n/m))^k` |
| `memory_usage_bytes()` | Bytes used by the bit array: `ceil(m/8)` |

**Properties:** `bit_array_size` (m), `num_hash_functions` (k), `item_count`, `expected_items` (n), `target_false_positive_rate` (p).

---

### `bloomcrawl/core/simhash.py` — Simhash

**What it does:** Generates a 64-bit fingerprint of page content. Two pages with Hamming distance ≤ threshold are "similar" (near-duplicates).

**Algorithm:**
1. Tokenise text into word-shingles (3-grams by default).
2. For each shingle, compute a 64-bit MD5 hash.
3. Maintain a 64-element weight vector: `weights[bit] += 1` if bit is set, else `−1`.
4. Final fingerprint: bit `i` = 1 if `weights[i] > 0`.

**Why Simhash over MD5/SHA?** MD5/SHA only detect *exact* duplicates. Simhash detects *near*-duplicates — pages that are 95% identical (mirrors, paginated versions, scraped copies) differ by only a few Hamming bits.

---

### `bloomcrawl/core/dedup_job.py` — DedupJob

**What it does:** Offline batch deduplication run on a schedule. Complements the online BloomFilter.

**MapReduce analogy:**
- Map: each URL occurrence emits `url → 1`
- Reduce: `collections.Counter` sums all counts
- Filter: keep only `count == 1` (truly new URLs)

**When to use:** Run daily against the URL frontier to clean up exact duplicates the BloomFilter may have missed (due to restarts or filter rotation).

---

### `bloomcrawl/crawler/page.py` — Page

Dataclass for a single crawled page. `signature` (Simhash) is auto-computed from `content` on `__post_init__`. `page_id` is the first 16 hex chars of `SHA-256(url)`.

---

### `bloomcrawl/crawler/url_frontier.py` — URLFrontier

Thread-safe priority queue backed by `queue.PriorityQueue` (stdlib heapq + mutex).

**Priority convention:** lower integer = crawl sooner. Seeds get priority 0; children get priority 1, 2, ... (BFS-like).

**Why not raw `heapq`?** `heapq` is not thread-safe. Concurrent `heappush`/`heappop` silently corrupts the heap without raising an error. `PriorityQueue` wraps it with a `threading.Condition` — no manual locking needed.

---

### `bloomcrawl/crawler/page_store.py` — PageStore

In-memory NoSQL-style store. Production equivalent: Cassandra or DynamoDB.

**Key method — `crawled_similar(sig)`:** O(n) Simhash scan across all stored pages. Returns True if any stored page has Hamming distance ≤ 3 from the given signature. Production replacement: prefix-band lookup table (split the 64-bit hash into 4 bands of 16 bits; index by band value for O(1) near-duplicate detection).

---

### `bloomcrawl/crawler/crawler.py` — Crawler

Orchestrates the full crawl loop. The fetcher is **injected** — in production pass a real HTTP client; in tests pass a mock graph (no network needed).

**Cycle prevention:** The BloomFilter is checked before every fetch. Since `add()` only ever flips bits 0→1 and `contains()` checks all k bits, a URL added in a previous iteration will always return True on the next visit — the crawler terminates.

---

### `bloomcrawl/workers/reverse_index.py` — ReverseIndex

Inverted index: `word → [(page_id, score)]`.

- Title words are scored 2.0 (more relevant), body words 1.0.
- `lookup_multi(words)` implements boolean AND with score summing.
- `benchmark_lookup(word, n)` returns p50/p95/p99 latency in ms.
- Interface is **swap-ready for Redis** (`HSET word page_id score` → `ZRANGEBYSCORE`).

---

### `bloomcrawl/frontend/query_parser.py` — QueryParser

4-step pipeline. Each step returns `ParsedQuery` or `ParseError` — **errors are typed, never silenced**. Callers map `ParseError` to HTTP 400.

| Step | What it does |
|---|---|
| `_validate` | Reject empty or >500-char queries |
| `_strip_markup` | Remove HTML tags with regex |
| `_tokenise` | Lowercase alphanumeric tokens |
| `_normalise` | Remove stop words, deduplicate |

---

### `bloomcrawl/frontend/cache_manager.py` — CacheManager

Akamai-pioneered two-tier cache. See [Akamai Caching Pattern](#akamai-caching-pattern) below.

---

## Key Design Decisions

| # | Decision | Why |
|---|---|---|
| 1 | `BloomFilter` standalone (not inside `PageStore`) | Single responsibility; independently testable and benchmarkable |
| 2 | `bitarray` (C ext) as bit backing store | 8× memory vs `bytearray`; benchmark comparisons are fair |
| 3 | MurmurHash3 for k hash functions | Non-cryptographic, fast, strong avalanche property; ~10× faster than SHA-256 |
| 4 | Double hashing: `h_i = (h1 + i·h2) % m` | 2 hash calls + k cheap linear combos; same FPR as k independent functions (Kirsch & Mitzenmacher 2006) |
| 5 | `h2 \| 1` (force odd step) | Odd step with any modulus visits all m positions before cycling — prevents clustering |
| 6 | m and k derived from `(n, p)` | Correct FPR for any parameter; no magic constants; same as Redis/Cassandra |
| 7 | `queue.PriorityQueue` for `URLFrontier` | Thread-safe stdlib wrapper; no manual locking required |
| 8 | 64-bit Simhash for `Page.signature` | Near-duplicate detection via Hamming distance — catches mirrors, paginated copies |
| 9 | `CacheManager` holds second `BloomFilter` | Akamai pattern as first-class testable component |
| 10 | `QueryParser` returns typed `Result` | Explicit over clever; each step individually unit-testable; errors map to HTTP 400 |
| 11 | `DedupJob` via `collections.Counter` | MapReduce analogy (map→count→filter); offline complement to online BloomFilter |
| 12 | `ReverseIndex` wraps `dict` with swap interface | Fast for MVP; interface unchanged when switching to Redis |
| 13 | Fetcher injected into `Crawler` | No network needed in tests; mock graph makes cycle tests deterministic |

---

## Bloom Filter Deep Dive

### Why it works

A Bloom Filter is a bit array of `m` bits (all zeros at start).

**To ADD item x:**
- Compute k positions: `pos_i = (h1(x) + i·h2(x)) % m`
- Set `bits[pos_i] = 1` for all i

**To CHECK item x:**
- Compute the same k positions
- If ALL k bits are 1 → "probably yes" (possible false positive)
- If ANY bit is 0 → "definitely no" (impossible false negative)

**Why no false negatives?** `add()` only ever flips 0→1. Once all k bits are set for an item, they stay set. `contains()` will always find them.

**Why false positives?** k bits set by *different* items can accidentally all land at the k positions of a new item.

### Parameter formulas

```
m = ceil(-n · ln(p) / (ln 2)²)   # optimal bit array size
k = round((m/n) · ln 2)           # optimal hash function count
```

These are derived by minimising FPR as a function of m and k. The same formulas are used by Redis, Apache Cassandra, and Google Guava.

### FPR formula

After inserting n items:
```
FPR = (1 − e^(−k·n/m))^k
```

Our empirical FPR (measured by checking non-inserted items) matches this formula within ±2% across all tested `(n, p)` combinations — verified in `tests/test_bloom_filter.py::TestFPR`.

### Memory advantage

For N=100,000 URLs at p=0.01:

| Structure | Memory |
|---|---|
| BloomFilter | ~117 KB |
| Python `set()` | ~12 MB |
| **Ratio** | **~107× smaller** |

---

## Akamai Caching Pattern

Traditional caches cache everything. This wastes memory on one-shot queries that will never be requested again.

**Akamai's insight:** Use a Bloom Filter as a cache admission gate.

```
Query arrives
    │
    ├── BloomFilter.contains(query)?
    │     NO  → "definitely first request"
    │           Record in BloomFilter. Return live result. Do NOT cache.
    │
    │     YES → "probably seen before"
    │           Check Redis.
    │             HIT  → return cached result
    │             MISS → fetch live, cache in Redis, return result
```

**Effect:** Only queries seen ≥2 times enter Redis. One-shot queries (majority of long-tail traffic) never pollute the cache. This dramatically increases cache hit rates for popular queries.

In BloomCrawl this is implemented in `CacheManager`. The `BloomFilter` inside `CacheManager` is a *second* filter — independent from the URL-dedup filter in `Crawler`.

---

## Running the Project

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install mmh3 bitarray pytest pytest-benchmark simhash redis matplotlib
```

### Tests

```bash
# Full test suite (87 tests)
pytest tests/ -v

# Single file
pytest tests/test_bloom_filter.py -v
```

### Benchmarks

```bash
# Timing benchmarks (BloomFilter vs set: insert, lookup, memory)
pytest benchmarks/bench_bloom_filter.py --benchmark-only -v

# Memory comparison printout
pytest benchmarks/bench_bloom_filter.py::test_memory_comparison -v -s
```

### Plots

```bash
# Generate all 4 plots → benchmarks/plots/
python benchmarks/plot_benchmarks.py
```

**Plots generated:**

| File | What it shows |
|---|---|
| `1_fpr_vs_fill.png` | Empirical vs theoretical FPR as filter fills past capacity |
| `2_memory_comparison.png` | Memory (KB) log-log: BloomFilter vs set() from N=1K to 500K |
| `3_insert_speed.png` | Insert µs/op: BloomFilter vs set() across N |
| `4_lookup_speed.png` | Lookup µs/op for hits and misses: BloomFilter vs set() |

---

## Benchmark Results

*(Measured on Apple M-series, Python 3.13, N=100,000 URLs, p=0.01)*

### Memory

| Structure | Memory | Ratio |
|---|---|---|
| BloomFilter | 117 KB | 1× |
| set() | ~12 MB | 107× larger |

### Speed (rough order of magnitude)

| Operation | BloomFilter | set() |
|---|---|---|
| Insert | ~1–3 µs | ~0.3–0.5 µs |
| Lookup hit | ~1–2 µs | ~0.1–0.3 µs |
| Lookup miss | ~1–2 µs | ~0.1–0.2 µs |

**Takeaway:** `set()` is faster per operation (single hash, no bit array indirection). `BloomFilter` trades ~5–10× speed for ~100× memory savings. At crawler scale (billions of URLs), the memory saving is not optional — a `set()` of 1 billion URLs would require ~200 GB of RAM; a BloomFilter with p=0.01 requires ~1.2 GB.
