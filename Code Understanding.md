# BloomCrawl — Code Walkthrough
> Explanation of every file with full mathematical derivations.

---

## Table of Contents
1. [bloom_filter.py](#1-bloomcrawlcorebloom_filterpy)
2. [simhash.py](#2-bloomcrawlcoresimhashpy)
3. [dedup_job.py](#3-bloomcrawlcorededup_jobpy)
4. [page.py](#4-bloomcrawlcrawlerpagepy)
5. [url_frontier.py](#5-bloomcrawlcrawlerurl_frontierpy)
6. [page_store.py](#6-bloomcrawlcrawlerpage_storepy)
7. [crawler.py](#7-bloomcrawlcrawlercrawlerpy)
8. [reverse_index.py](#8-bloomcrawlworkersreverse_indexpy)
9. [reverse_index_worker.py](#9-bloomcrawlworkersreverse_index_workerpy)
10. [doc_index_worker.py](#10-bloomcrawlworkersdoc_index_workerpy)
11. [query_parser.py](#11-bloomcrawlfrontendquery_parserpy)
12. [cache_manager.py](#12-bloomcrawlfrontendcache_managerpy)

---

## 1. `bloomcrawl/core/bloom_filter.py`

### The Core Idea
A Bloom Filter answers one question: **"Have I seen this item before?"**
It uses a fixed-size bit array of `m` bits and `k` hash functions. It **never** gives false negatives (if it says "no", the item was definitely never inserted). It **may** give false positives (if it says "yes", the item was probably, but not certainly, inserted).

### Mathematical Foundation

**Given:** expected item count `n`, target false-positive rate `p ∈ (0,1)`

**Optimal bit-array size:**
```
m = ⌈ -n · ln(p) / (ln 2)² ⌉
```
**Derivation:**
The false positive rate of a Bloom Filter after inserting `n` items into `m` bits with `k` hash functions is:
```
FPR = (1 - e^(-kn/m))^k
```
Minimising FPR over `k` gives the optimal:
```
k* = (m/n) · ln 2
```
Substituting `k*` back and solving for `m` at target FPR = `p`:
```
p = (1/2)^k*   →   m = -n · ln(p) / (ln 2)²
```
We add `+1` as a safety margin to handle ceiling rounding edge cases.

**Optimal number of hash functions:**
```
k = round((m/n) · ln 2),  clamped to [1, 20]
```
The clamp `[1, 20]` prevents degenerate cases (k=0 means no checking; k>20 wastes CPU with diminishing FPR returns).

### Line-by-Line

```python
import math, mmh3
from bitarray import bitarray
```
- `math`: for `math.log`, `math.ceil`, `math.exp` — no surprises.
- `mmh3`: MurmurHash3 — a non-cryptographic hash, designed for speed and uniform distribution.
- `bitarray`: C extension providing a true bit-level array. A Python `bytearray` wastes 8× memory (1 byte per conceptual bit). `bitarray` stores 8 bits per byte, hence the 8× memory saving quoted in the docstring.

```python
class BloomFilter:
```
Standalone class — no external state. Can be constructed with any `(n, p)` pair.

```python
def __init__(self, expected_items: int, false_positive_rate: float) -> None:
```
Typed signature. `-> None` is explicit contract: `__init__` never returns a value.

```python
    if expected_items < 1:
        raise ValueError(...)
    if not (0 < false_positive_rate < 1):
        raise ValueError(...)
```
Guard clauses at the top. `n=0` would produce `m=0`, causing division-by-zero or zero-size arrays. `p≤0` or `p≥1` make the formulas undefined (log of zero/negative, or log of 1 = 0).

```python
    self._n = expected_items
    self._p = false_positive_rate
```
Store originals for introspection via properties.

```python
    self._m = math.ceil(-expected_items * math.log(false_positive_rate) / math.log(2) ** 2) + 1
```
This is the formula `m = ⌈-n·ln(p)/(ln2)²⌉ + 1` directly.
- `math.log(false_positive_rate)` → `ln(p)` which is **negative** (since `p < 1`).
- Negating it → positive.
- `math.log(2) ** 2` → `(ln 2)² ≈ 0.4804`.
- `math.ceil(...)` → rounds up, ensuring we never undersize.
- `+ 1` → safety margin.

```python
    self._k = max(1, min(20, round((self._m / expected_items) * math.log(2))))
```
`k* = (m/n)·ln2`.
- `round(...)` → nearest integer (not floor/ceiling — we want closest to optimum).
- `max(1, min(20, ...))` → double clamp: never 0 (useless), never >20 (CPU waste).

```python
    self._bits = bitarray(self._m)
    self._bits.setall(0)
```
Allocate `m` bits, all initialised to 0. `bitarray(m)` allocates but does NOT zero-initialise (C memory may contain garbage). `.setall(0)` is mandatory.

```python
    self._count = 0
```
Tracks how many items have been inserted. Used in the live FPR formula.

```python
def add(self, item: str) -> None:
    for pos in self._positions(item):
        self._bits[pos] = 1
    self._count += 1
```
Sets all `k` bit positions to 1. Note: setting a bit that's already 1 is a no-op — this is intentional and safe. The count always increments, even for re-insertions (slightly overestimates, acceptable).

```python
def contains(self, item: str) -> bool:
    return all(self._bits[pos] for pos in self._positions(item))
```
Checks all `k` positions. `all(...)` short-circuits on the first 0-bit found (fast miss path). Only if ALL k bits are 1 does it return True.

**Why no false negatives?** `add()` only flips bits `0→1`, never `1→0`. Once a bit is set, it stays set. So if item X was added, all k of its positions are 1 forever. `contains(X)` will always return True.

**Why false positives?** If items A, B, C happen to set all k positions that item X would map to, `contains(X)` returns True even though X was never inserted.

```python
def false_positive_rate(self) -> float:
    return (1.0 - math.exp(-self._k * self._count / self._m)) ** self._k
```
Live FPR estimate:
```
FPR(t) = (1 - e^(-k · count / m))^k
```
- `k · count / m` = expected number of 1-bits that a single hash function position is 1.
- `e^(-k·count/m)` = probability that a given bit is still 0.
- `(1 - e^(-k·count/m))^k` = probability that ALL k positions are 1 for a random item.

As `count → 0`, FPR → 0. As `count → ∞`, FPR → 1 (all bits are 1).

```python
def memory_usage_bytes(self) -> int:
    return self._bits.buffer_info()[1]
```
`bitarray.buffer_info()` returns `(address, nbytes, ...)`. Index `[1]` is the actual byte count of the underlying C buffer. This is the true memory usage.

```python
def _positions(self, item: str) -> list[int]:
    b = item.encode()
    h1 = mmh3.hash(b, seed=0, signed=False)
    h2 = mmh3.hash(b, seed=1, signed=False) | 1
    return [(h1 + i * h2) % self._m for i in range(self._k)]
```
**Double hashing** (Kirsch & Mitzenmacher, 2006):

Instead of k independent hash functions (expensive — k separate hash computations), use:
```
h_i(x) = (h1(x) + i · h2(x)) mod m,   i = 0, 1, ..., k-1
```
This requires **exactly 2 hash calls** regardless of k. The paper proves this achieves the same asymptotic FPR as k truly independent hash functions.

- `mmh3.hash(b, seed=0, signed=False)` → 32-bit unsigned `h1`.
- `mmh3.hash(b, seed=1, signed=False)` → 32-bit unsigned raw `h2`.
- `| 1` → **force h2 odd**. Why? If `m` is even and `h2` is even, then `h1 + i·h2` mod `m` cycles through only `m/gcd(h2,m) = m/m = 1` position — it gets stuck! Forcing odd step ensures `gcd(h2, m) = 1` when m is a power of 2, visiting all m positions before repeating.
- `% self._m` → wrap into `[0, m)`.

---

## 2. `bloomcrawl/core/simhash.py`

### The Core Idea
Simhash produces a 64-bit fingerprint such that **similar documents produce fingerprints with small Hamming distance**. It was invented by Moses Charikar (2002) and used by Google for near-duplicate detection at web scale.

### Mathematical Foundation

**Hamming Distance:**
The number of bit positions where two binary strings differ.
```
hamming(a, b) = popcount(a XOR b)
```
where `popcount` counts the number of 1-bits.

**Simhash Construction:**
1. Decompose text into shingles (n-gram tokens).
2. Hash each shingle to a 64-bit value.
3. For each bit position `j ∈ [0, 63]`:
   - Add `+1` if bit `j` of the hash is 1.
   - Add `-1` if bit `j` of the hash is 0.
4. Final fingerprint: bit `j` = 1 if `weight[j] > 0`, else 0.

**Intuition:** Two documents sharing many shingles will have similar weight vectors, producing nearly identical fingerprints. One different word shifts only a few weights, flipping only a few bits.

### Line-by-Line

```python
import hashlib
from dataclasses import dataclass
```
- `hashlib`: for MD5 — used here as a fast hash (not for security; 128-bit output truncated to 64 bits).
- `dataclass(frozen=True)`: makes `Simhash` immutable and hashable (can be stored in sets/dicts).

```python
@dataclass(frozen=True)
class Simhash:
    value: int
```
Single field: the 64-bit fingerprint as a Python int. `frozen=True` means no attribute can be changed after creation — important since `Page` stores this as a content identity.

```python
@classmethod
def from_text(cls, text: str, shingle_size: int = 3) -> "Simhash":
```
Class method (factory) — creates a `Simhash` from raw text. `shingle_size=3` means tri-grams of words. `"Simhash"` in quotes is a forward reference (class not yet fully defined at parse time).

```python
    tokens = text.lower().split()
```
Lowercase and split on whitespace. No punctuation stripping — simple and fast.

```python
    shingles = (
        [" ".join(tokens[i:i + shingle_size])
         for i in range(max(1, len(tokens) - shingle_size + 1))]
        or [text or " "]
    )
```
**Sliding window shingles:**
For tokens `[w0, w1, w2, w3, w4]` with `shingle_size=3`:
```
shingles = ["w0 w1 w2", "w1 w2 w3", "w2 w3 w4"]
```
Number of shingles = `max(1, len(tokens) - shingle_size + 1)`.

`max(1, ...)` ensures at least 1 iteration even for short texts.
`or [text or " "]` handles the edge case: empty `tokens` list → shingles would be `[]` → fall back to the raw text (or `" "` if also empty). This prevents an all-zero fingerprint from empty inputs.

```python
    weights = [0] * 64
```
64-element list of signed integers. Indices 0..63 correspond to bit positions of the final fingerprint.

```python
    for s in shingles:
        h = int(hashlib.md5(s.encode()).hexdigest(), 16) & ((1 << 64) - 1)
```
- `hashlib.md5(s.encode()).hexdigest()` → 32-character hex string of 128-bit MD5.
- `int(..., 16)` → convert hex string to Python int (128 bits).
- `& ((1 << 64) - 1)` → mask to low 64 bits: `0xFFFFFFFFFFFFFFFF`.

```python
        for bit in range(64):
            weights[bit] += 1 if (h >> bit) & 1 else -1
```
For each of the 64 bits:
- `(h >> bit) & 1` → extract bit `bit` from the hash (0 or 1).
- If 1: `weights[bit] += 1` (vote for this bit being 1 in the fingerprint).
- If 0: `weights[bit] -= 1` (vote for this bit being 0).

```python
    return cls(value=sum((1 << b) for b in range(64) if weights[b] > 0))
```
Construct fingerprint: bit `b` is 1 iff `weights[b] > 0` (majority vote).
`sum((1 << b) for ...)` builds the 64-bit int by ORing in each set bit.

```python
def hamming_distance(self, other: "Simhash") -> int:
    return bin(self.value ^ other.value).count("1")
```
- `self.value ^ other.value` → XOR: bits that differ become 1.
- `bin(...)` → binary string `"0b10110..."`.
- `.count("1")` → count of differing bits = Hamming distance.

This is `O(1)` in bit-width — Python's `bin().count()` operates on the machine-native int XOR result.

```python
def is_similar(self, other: "Simhash", threshold: int = 3) -> bool:
    return self.hamming_distance(other) <= threshold
```
Default `threshold=3`: fingerprints differing in ≤3 of 64 bits are "near-duplicate". In a random 64-bit space, two random documents would differ in ~32 bits on average. ≤3 bits difference is very strong similarity evidence.

---

## 3. `bloomcrawl/core/dedup_job.py`

### The Core Idea
Offline (batch) URL deduplication — the complement to the online BloomFilter gate. Think of it as a scheduled nightly cleanup job. Maps to MapReduce: map each URL to 1, reduce by summing counts.

### Line-by-Line

```python
from collections import Counter
from typing import Iterable
```
- `Counter`: dict subclass. `Counter(["a","b","a"])` → `{"a":2,"b":1}`.
- `Iterable`: accepts any sequence type — list, generator, set, etc.

```python
@staticmethod
def deduplicate(urls: Iterable[str]) -> list[str]:
    return [u for u, c in Counter(urls).items() if c == 1]
```
**True deduplication:** only keeps URLs that appear **exactly once** (genuinely unique). A URL appearing twice is considered a duplicate and is removed entirely.

MapReduce analogy:
- **Map:** each URL → emit `(url, 1)`.
- **Reduce:** sum counts → `(url, count)`.
- **Filter:** keep only `count == 1`.

```python
@staticmethod
def find_duplicates(urls: Iterable[str]) -> dict[str, int]:
    return {u: c for u, c in Counter(urls).items() if c > 1}
```
Returns a dict of `url → count` for all URLs seen more than once. Useful for reporting/monitoring.

```python
@staticmethod
def unique_urls(urls: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(urls))
```
**Order-preserving deduplication** — keeps the first occurrence of each URL.
`dict.fromkeys(iterable)` creates a dict with keys in insertion order (Python 3.7+), discarding duplicates. Converting back to list preserves order. This is the fastest order-preserving dedup in Python — O(n) time and space.

---

## 4. `bloomcrawl/crawler/page.py`

### The Core Idea
Immutable data record for a crawled web page. The `signature` field is automatically computed from content — it can never be set incorrectly by a caller.

### Line-by-Line

```python
import hashlib
from dataclasses import dataclass, field
from bloomcrawl.core.simhash import Simhash
```
- `hashlib`: for SHA-256 URL hashing.
- `dataclass, field`: declarative class with auto-generated `__init__`, `__repr__`, `__eq__`.
- `field(default_factory=list)`: needed for mutable defaults (a plain `= []` would share one list across all instances — a classic Python gotcha).

```python
@dataclass
class Page:
```
Not `frozen=True` here — Page is mutable (though in practice we don't mutate it). `frozen=True` would prevent `__post_init__` from assigning `self.signature`.

```python
    signature: Simhash = field(init=False)
```
`init=False` means `signature` is **not** a constructor parameter. It cannot be passed in. This enforces invariant: signature is always derived from content, never manually set.

```python
    def __post_init__(self) -> None:
        self.signature = Simhash.from_text(self.content)
```
`__post_init__` is called automatically by `@dataclass` after `__init__`. Here we compute the Simhash fingerprint from `self.content`. This runs exactly once, at construction time.

```python
    @classmethod
    def from_raw(cls, url: str, title: str, content: str, child_urls: list[str]) -> "Page":
        return cls(
            page_id=hashlib.sha256(url.encode()).hexdigest()[:16],
```
`page_id` = first 16 hex characters of SHA-256(URL). SHA-256 produces 64 hex chars (256 bits). We take 16 = 64 bits of collision resistance. At 1 billion pages, the collision probability is:
```
P(collision) ≈ n² / (2 · 2^64) ≈ 10^18 / (3.7 × 10^19) ≈ 2.7%
```
Acceptable for a learning project; production uses full 256 bits.

```python
            snippet=content[:200].strip(),
```
First 200 characters of content, whitespace-stripped. Used for search result previews.

---

## 5. `bloomcrawl/crawler/url_frontier.py`

### The Core Idea
A priority queue of URLs to crawl next. "Priority" here means urgency — lower integer = crawl sooner. Seed URLs (the starting points) get priority 0. Discovered child URLs get priority 1 (crawl after seeds are exhausted).

### Line-by-Line

```python
import queue
from dataclasses import dataclass, field
```
- `queue.PriorityQueue`: thread-safe min-heap from stdlib. Uses `heapq` internally plus a `threading.Lock`. No external locking needed.

```python
@dataclass(order=True)
class _Entry:
    priority: int
    url: str = field(compare=False)
```
`order=True` generates `__lt__`, `__le__`, etc. from fields in **declaration order**. The heap compares `_Entry` objects — so it must know how to order them.

`field(compare=False)` on `url` means: when comparing two `_Entry` objects, only use `priority`. Without this, if two entries had the same priority, Python would compare `url` strings lexicographically — which is meaningless for crawl ordering and could cause surprising behaviour with non-comparable types.

```python
class URLFrontier:
    def __init__(self) -> None:
        self._q: queue.PriorityQueue[_Entry] = queue.PriorityQueue()
```
Type annotation `queue.PriorityQueue[_Entry]` is documentation — Python doesn't enforce generics at runtime, but IDEs and type checkers use it.

```python
    def put(self, url: str, priority: int = 0) -> None:
        self._q.put(_Entry(priority, url))
```
Wraps url + priority into an `_Entry` and enqueues it. The heap property is maintained by `PriorityQueue`.

```python
    def get(self, block: bool = True, timeout: float | None = None) -> str:
        return self._q.get(block=block, timeout=timeout).url
```
Dequeues the minimum-priority `_Entry` and returns just the URL string. `block=True` means the caller waits if the queue is empty (useful in multi-threaded workers). `timeout=None` means wait forever.

```python
    def task_done(self) -> None: self._q.task_done()
    def empty(self) -> bool:     return self._q.empty()
    def size(self) -> int:       return self._q.qsize()
```
Thin wrappers. `task_done()` is required for `queue.join()` to work correctly — it signals that the consumer has finished processing the dequeued item. Essential for graceful shutdown.

---

## 6. `bloomcrawl/crawler/page_store.py`

### The Core Idea
Thread-safe in-memory store for crawled pages. Production equivalent: Cassandra (wide-column NoSQL, optimised for write-heavy workloads) or DynamoDB (managed key-value).

### Line-by-Line

```python
import threading
```
`threading.RLock()` = **re-entrant lock**. Unlike a regular `Lock`, an `RLock` allows the **same thread** to acquire it multiple times without deadlocking. This is important because some `PageStore` methods could call other methods internally.

```python
    self._pages: dict[str, Page] = {}           # page_id → Page
    self._crawled: set[str] = set()             # URLs successfully crawled
    self._pending: dict[str, int] = {}          # url → priority (to-crawl frontier)
```
Three data structures:
- `_pages`: primary document store, keyed by page_id (16-char hex).
- `_crawled`: fast O(1) membership test for "have we crawled this URL?".
- `_pending`: frontier of URLs we know about but haven't crawled yet.

```python
def insert_crawled_link(self, page: Page) -> None:
    with self._lock:
        self._pages[page.page_id] = page
        self._crawled.add(page.url)
        self._pending.pop(page.url, None)
```
`with self._lock` is a context manager — acquires lock on entry, releases on exit (even if an exception occurs). `_pending.pop(url, None)` removes from pending if present, does nothing if not (the `None` default prevents `KeyError`).

```python
def crawled_similar(self, sig: Simhash) -> bool:
    """O(n) Simhash scan — acceptable at MVP scale. Production: prefix-band lookup table."""
    with self._lock:
        return any(p.signature.is_similar(sig, _SIM_THRESHOLD) for p in self._pages.values())
```
`any(...)` short-circuits: stops scanning on the first similar page found.
**O(n) complexity** — scans all stored pages. For a million pages with 64-bit Simhashes:
- Each comparison: 1 XOR + 1 popcount ≈ 2 CPU ops.
- Total: ~2M ops per URL — acceptable for MVP, too slow for production.

**Production approach:** Simhash prefix-band lookup. Split the 64-bit fingerprint into B bands of R bits each. Two signatures with Hamming distance ≤ D will share at least one identical band. Index each band in a hash table → O(1) lookup.

```python
_SIM_THRESHOLD = 3  # Hamming distance <= 3 → near-duplicate
```
Module-level constant. Hamming distance of 3 means: at most 3 of 64 bits differ. Probability that two random 64-bit numbers have Hamming distance ≤ 3:
```
P = sum(C(64,k) / 2^64 for k in [0,1,2,3])
  = (1 + 64 + 2016 + 41664) / 2^64
  ≈ 43745 / 1.84×10^19
  ≈ 2.4 × 10^-15
```
Essentially zero — so threshold=3 is very conservative and has almost no false positive near-duplicate detection for genuinely different content.

---

## 7. `bloomcrawl/crawler/crawler.py`

### The Core Idea
The orchestration layer. Implements the 7-step crawl loop. Fully testable via injected `fetcher` function — tests never make real HTTP requests.

### Line-by-Line

```python
Fetcher = Callable[[str], tuple[str, str, list[str]]]  # url → (title, content, child_urls)
IndexMessage = dict[str, str]
```
Type aliases at module level. `Fetcher` is a function type: takes a URL string, returns `(title, content, child_urls)`. This defines the contract any `fetcher` (real or mock) must satisfy. `IndexMessage` is a plain dict (typed for clarity).

```python
def _fetch(url: str) -> tuple[str, str, list[str]]:
    with urllib.request.urlopen(url, timeout=10) as r:
        html = r.read().decode("utf-8", errors="ignore")
```
Real HTTP fetcher (used in production). `timeout=10` seconds. `errors="ignore"` — silently drops bytes that aren't valid UTF-8 rather than raising `UnicodeDecodeError`.

```python
    m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    title = m.group(1).strip() if m else url
```
Extracts `<title>` content. `re.I` = case-insensitive (handles `<TITLE>`). `re.S` = dot matches newlines (multi-line titles). Falls back to URL string if no title tag found.

```python
    text = re.sub(r"<[^>]+>", " ", html)
```
Strips all HTML tags (replaces `<anything>` with a space). Not a full HTML parser — doesn't handle edge cases like `>` in attributes. Sufficient for this project.

```python
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
```
Extracts all `href` attribute values. Captures both `"..."` and `'...'` delimiters. These become child URLs for the frontier.

```python
class Crawler:
    def __init__(self, seed_urls: list[str], ...) -> None:
        self.bloom_filter = bloom_filter or BloomFilter(max_pages * 2, 0.01)
```
Default BloomFilter sized for `2 × max_pages` items. The `×2` factor accounts for: (1) URLs we see but don't crawl (near-duplicates, errors), and (2) URLs in the frontier that may never be crawled. Overprovisioning reduces FPR.

```python
        self.reverse_index_queue: queue.Queue[IndexMessage] = queue.Queue()
        self.doc_index_queue:     queue.Queue[IndexMessage] = queue.Queue()
```
Two separate queues for the two worker types. Using unbounded `queue.Queue()` — workers drain them asynchronously. In production, these would be Kafka topics.

```python
        for url in seed_urls:
            self.frontier.put(url, priority=0)
```
Seeds get priority 0 — they're crawled first. All discovered children will get priority 1.

```python
    def crawl(self) -> None:
        crawled = 0
        while not self.frontier.empty() and crawled < self.max_pages:
```
Loop terminates when either: (a) the frontier is exhausted, or (b) we've hit the max_pages budget. The budget prevents runaway crawls.

```python
            if self.bloom_filter.contains(url):
                self.frontier.task_done(); continue
```
**BloomFilter gate.** If the URL was probably seen before, skip it immediately. No fetch, no Simhash. This is the primary deduplication mechanism — O(k) time (constant for fixed k).

```python
            try:
                title, content, children = self.fetcher(url)
            except Exception:
                self.frontier.task_done(); continue
```
Broad `except Exception` — silently skips any fetch failure (network error, timeout, 404, etc.). In production, you'd log these and potentially retry with exponential backoff.

```python
            if self.data_store.crawled_similar(page.signature):
                self.bloom_filter.add(url)    # ← block future fetches of this URL
                self.frontier.task_done(); continue
```
**Simhash gate.** Near-duplicate content detected — don't store the page, but DO add the URL to the BloomFilter so we don't fetch it again. This is a subtle but important detail.

```python
            self.bloom_filter.add(url)
            self.data_store.insert_crawled_link(page)
            crawled += 1
```
Commit the page. `bloom_filter.add()` before `insert_crawled_link()` — even if the store insert fails (exception), the URL is marked as seen and won't be re-fetched.

```python
            msg: IndexMessage = {
                "page_id": page.page_id, "url": page.url,
                "title": page.title, "snippet": page.snippet, "content": page.content,
            }
            self.reverse_index_queue.put(msg)
            self.doc_index_queue.put(msg)
```
Fan-out: publish the same message to two queues. Workers consume asynchronously. `queue.Queue.put()` is thread-safe and non-blocking (unbounded queue never blocks on put).

---

## 8. `bloomcrawl/workers/reverse_index.py`

### The Core Idea
Inverted index mapping each word to a ranked list of pages containing that word. The "reverse" means: instead of `page → words`, we store `word → pages`. This enables O(1) per-word lookup (vs O(n) full scan).

### Line-by-Line

```python
from collections import defaultdict
```
`defaultdict(list)`: a dict that auto-creates an empty list for missing keys. Eliminates the `if key not in d: d[key] = []` boilerplate.

```python
    self._idx: dict[str, list[tuple[str, float]]] = defaultdict(list)
```
Type: `word → [(page_id, score), ...]`. Score is `float` to support fractional weights (title=2.0, body=1.0).

```python
def add(self, word: str, page_id: str, score: float = 1.0) -> None:
    with self._lock:
        self._idx[word].append((page_id, score))
```
O(1) append. No deduplication here — `ReverseIndexWorker` ensures each word-page pair is only added once per page.

```python
def lookup(self, word: str) -> list[tuple[str, float]]:
    with self._lock:
        return sorted(self._idx.get(word, []), key=lambda x: x[1], reverse=True)
```
`sorted(..., reverse=True)` → highest-scored pages first. Returns a copy (new sorted list) — the caller can't corrupt the internal structure.

```python
def lookup_multi(self, words: list[str]) -> list[tuple[str, float]]:
    """Boolean AND across words, scored by sum."""
    sets = [set(pid for pid, _ in self._idx.get(w, [])) for w in words]
    common = sets[0].intersection(*sets[1:])
```
**Boolean AND:** only pages containing ALL query words. `set.intersection()` is O(n) where n is the smallest set size.

```python
    scores: dict[str, float] = defaultdict(float)
    for w in words:
        for pid, s in self._idx.get(w, []):
            if pid in common:
                scores[pid] += s
```
Score = sum of per-word scores. A page scoring 2.0 (title hit) + 1.0 (body hit) for a two-word query gets score 3.0.

```python
def benchmark_lookup(self, word: str, n: int = 1000) -> dict[str, float]:
    lats = []
    for _ in range(n):
        t0 = time.perf_counter()
        self.lookup(word)
        lats.append((time.perf_counter() - t0) * 1000)
    lats.sort()
    return {
        "p50_ms": statistics.median(lats),
        "p95_ms": lats[int(0.95 * n)],
        "p99_ms": lats[int(0.99 * n)],
    }
```
Percentile latency measurement. `perf_counter()` has nanosecond resolution. `× 1000` converts seconds to milliseconds. p50 = median (50th percentile), p95/p99 = tail latencies. Sort first, then index: `lats[0.95 * n]` is the value below which 95% of measurements fall.

---

## 9. `bloomcrawl/workers/reverse_index_worker.py`

### The Core Idea
Daemon thread that consumes the `reverse_index_queue`. Processes each `IndexMessage` and populates the `ReverseIndex`. Title words get 2× weight — the assumption is that words in a title are more representative of a page's topic.

### Line-by-Line

```python
    self._t = threading.Thread(target=self._run, daemon=True)
```
`daemon=True`: this thread dies automatically when the main program exits. Without this, the program would hang waiting for the thread to finish.

```python
def stop(self) -> None:
    self._q.put(None)   # poison pill
    self._t.join(timeout=5)
```
**Poison pill pattern**: instead of using a shared stop flag (which requires a lock), we send a sentinel value (`None`) through the queue itself. The worker's `_run` loop treats `None` as the shutdown signal. `join(timeout=5)` waits up to 5 seconds for the thread to exit cleanly.

```python
def _run(self) -> None:
    while True:
        try:
            msg = self._q.get(timeout=1)
        except queue.Empty:
            continue
```
Non-blocking get with 1-second timeout. If the queue is empty, `queue.Empty` is raised → `continue` → retry. This allows the thread to check for shutdown periodically even if no messages arrive.

```python
        title_words = set(_tokenise(msg.get("title", "")))
        for word in set(_tokenise(msg.get("title", "") + " " + msg.get("content", ""))):
            self._idx.add(word, pid, score=2.0 if word in title_words else 1.0)
```
Two tokenise calls. The `set(...)` on the outer loop deduplicates: each unique word gets exactly one entry per page. `title_words` is a set for O(1) membership test on `if word in title_words`.

Score logic: if a word appears in the title, it gets score 2.0. Body-only words get 1.0. A word in both gets 2.0 (since it was added once, as `set(...)` deduplicates).

---

## 10. `bloomcrawl/workers/doc_index_worker.py`

### The Core Idea
Second fan-out worker — a stub for a separate document database (Elasticsearch, Postgres full-text, etc.). Currently a no-op beyond consuming messages from the queue, demonstrating the architectural pattern.

### Line-by-Line

```python
    self._t = threading.Thread(target=self._run, daemon=True)
```
Same daemon pattern as `ReverseIndexWorker`.

```python
    def _run(self) -> None:
        while True:
            ...
            if msg is None:
                break
            # PageStore already holds title/snippet; extend here for a separate doc DB.
            self._q.task_done()
```
The comment explains the design intent: `PageStore` already stores the full Page object. If you later add Elasticsearch, you'd call `es.index(...)` here. The queue infrastructure is ready — just fill in the stub.

---

## 11. `bloomcrawl/frontend/query_parser.py`

### The Core Idea
A typed pipeline that converts raw user input into structured search terms. Every error is a named return value (`ParseError`), never an exception that bubbles up silently. Callers can pattern-match on the return type.

### Pipeline Stages

```
raw str → [validate] → [strip_markup] → [tokenise] → [normalise] → ParsedQuery
                ↓              ↓               ↓             ↓
           ParseError     ParseError      ParseError    ParseError
```

### Line-by-Line

```python
_STOP = frozenset({
    "a","an","the","and","or","but","in","on","at","to","for","of","with","by","from","is","was",
})
```
Stop words: common English words with low discriminative value for search. `frozenset` for O(1) membership test and immutability.

```python
_MAX_LEN = 500
```
Hard cap at 500 characters. Protects against: (1) accidental paste of very long text, (2) deliberate DoS via extremely long queries.

```python
@dataclass(frozen=True)
class ParsedQuery:
    terms: list[str]
    raw: str

@dataclass(frozen=True)
class ParseError:
    reason: str
    raw: str
```
Two distinct return types. `frozen=True` makes both hashable and immutable.
`raw` is preserved in both — useful for logging: you can always trace what the original input was.

```python
Result = Union[ParsedQuery, ParseError]
```
Type alias. Callers write: `result: Result = parser.parse(query)`. Type checkers can warn if you forget to handle `ParseError`.

```python
    def parse(self, raw: str) -> Result:
        r = self._validate(raw)
        if isinstance(r, ParseError): return r
        r = self._strip_markup(r)
        if isinstance(r, ParseError): return r
        ...
```
**Railway-oriented programming** (also called "result types"). Each stage either succeeds (returns `ParsedQuery`) or fails (returns `ParseError`). On failure, the pipeline short-circuits immediately — subsequent stages are skipped.

```python
def _validate(self, raw: str) -> Result:
    if not raw or not raw.strip():
        return ParseError("query is empty", raw)
    if len(raw) > _MAX_LEN:
        return ParseError(f"query exceeds {_MAX_LEN} characters", raw)
    return ParsedQuery([raw], raw)
```
Note: returns `ParsedQuery([raw], raw)` — wraps the entire raw string as the single "term". Subsequent stages will refine this.

```python
def _strip_markup(self, q: ParsedQuery) -> Result:
    cleaned = re.sub(r"<[^>]+>", " ", q.terms[0]).strip()
    return ParsedQuery([cleaned], q.raw) if cleaned else ParseError("empty after markup removal", q.raw)
```
XSS defense: `<script>alert(1)</script>` → `" "` (stripped) → `""` (stripped of whitespace) → `ParseError`.

```python
def _tokenise(self, q: ParsedQuery) -> Result:
    tokens = re.findall(r"[a-zA-Z0-9]+", q.terms[0].lower())
    return ParsedQuery(tokens, q.raw) if tokens else ParseError("no alphanumeric tokens found", q.raw)
```
`re.findall(r"[a-zA-Z0-9]+", ...)` — extracts only alphanumeric sequences. Strips punctuation, SQL injection characters (`;`, `'`, `--`), Unicode symbols. Case-folded to lowercase.

```python
def _normalise(self, q: ParsedQuery) -> Result:
    seen: set[str] = set()
    terms = []
    for t in q.terms:
        if t not in _STOP and t not in seen and len(t) > 1:
            seen.add(t)
            terms.append(t)
    return ParsedQuery(terms, q.raw) if terms else ParseError("all tokens were stop words", q.raw)
```
Three filters applied simultaneously:
1. `t not in _STOP`: remove stop words.
2. `t not in seen`: deduplicate (don't search for "the cat and the cat" → just "cat").
3. `len(t) > 1`: remove single-character tokens (almost always noise: punctuation artifacts, initials).

`seen` is a set for O(1) dedup tracking. Order of `terms` is preserved (insertion order).

---

## 12. `bloomcrawl/frontend/cache_manager.py`

### The Core Idea
**Akamai two-tier cache** — originally described by Akamai engineers for CDN request routing.

**Problem:** Standard caching caches every request, including "one-shot" queries (e.g., a rare search term nobody else will ever type). These waste cache memory and can evict more useful entries.

**Solution:** Only cache queries seen at least twice:
- First occurrence: "probably unique" → don't cache, just record it was seen.
- Second+ occurrence: "probably repeated" → cache the result.

The BloomFilter is the "seen" oracle.

### Mathematical Edge Case: False Positives

Since the BloomFilter has a non-zero FPR (1% by default), a brand-new query might be mistakenly flagged as "probably seen" (false positive). In that case, we check the cache and find nothing (cold miss). This means:

- **Worst case:** ~1% of first-time queries trigger an unnecessary cache lookup.
- **No correctness error:** the cache miss is handled gracefully (returns `None`).

This is acceptable: we trade a tiny overhead on 1% of first-time queries for a guaranteed benefit on all repeated queries.

### Line-by-Line

```python
def __init__(self, redis_client: Any = None, ...) -> None:
    self._bloom = BloomFilter(expected_queries, false_positive_rate)
    self._redis = redis_client
    self._ttl   = ttl_seconds
    self._local: dict[str, Any] = {}
```
`redis_client: Any = None` — dependency injection. Pass a `redis.Redis()` instance in production. Pass nothing for in-process dict fallback (testing, development).

```python
def get(self, query: str) -> Any | None:
    seen = self._bloom.contains(query)
    self._bloom.add(query)           # ← always record this visit
    return self._read(query) if seen else None
```
Two operations happen on every `get()`:
1. Check if seen before (`contains`).
2. Record this visit (`add`).

The order matters: check first, then add. If you add first, every query appears "seen" on its own first call.

**Sequence for a new query Q:**
- Call 1: `contains(Q)=False` → `add(Q)` → return `None`. *(Cache miss)*
- Call 2: `contains(Q)=True` → `add(Q)` → `_read(Q)` → return `None`. *(Cache miss — not yet stored)*
- Producer calls `set(Q, result)` → result stored in Redis/local.
- Call 3: `contains(Q)=True` → `_read(Q)` → return `result`. *(Cache hit!)*

```python
def set(self, query: str, result: Any) -> None:
    if self._redis is not None:
        self._redis.setex(query, self._ttl, json.dumps(result))
    else:
        self._local[query] = result
```
`redis.setex(key, ttl_seconds, value)` — set with automatic expiry. Values are JSON-serialised (Redis stores bytes/strings, not Python objects). Local dict has no TTL — suitable only for testing.

```python
def _read(self, query: str) -> Any | None:
    if self._redis is not None:
        raw = self._redis.get(query)
        return json.loads(raw) if raw is not None else None
    return self._local.get(query)
```
`redis.get()` returns `None` if key doesn't exist or has expired. `json.loads()` deserialises back to Python object. The `if raw is not None` guard prevents `json.loads(None)` crash.

---

## Summary Table

| File | Key Algorithm | Time Complexity | Space Complexity |
|------|--------------|-----------------|------------------|
| `bloom_filter.py` | Double hashing | O(k) per op | O(m) bits |
| `simhash.py` | Weight-vote fingerprinting | O(S·64) per page | O(64) per sig |
| `dedup_job.py` | Counter (MapReduce) | O(n) | O(unique URLs) |
| `page.py` | SHA-256 page_id | O(URL len) | O(1) per page |
| `url_frontier.py` | Min-heap priority queue | O(log n) per op | O(n) |
| `page_store.py` | Hash map + RLock | O(1) per page op | O(n pages) |
| `crawler.py` | BFS + gates | O(pages × k) | O(m + n pages) |
| `reverse_index.py` | Inverted index | O(1) add, O(r log r) lookup | O(total words) |
| `query_parser.py` | Pipeline pattern | O(query len) | O(tokens) |
| `cache_manager.py` | Bloom-gated LRU | O(k) per get | O(m) + cache |

**Where:**
- `k` = number of hash functions in BloomFilter (typically 7 for 1% FPR)
- `m` = bit array size (≈ 9.58 × n bits for 1% FPR)
- `S` = number of shingles in a document
- `r` = number of results for a given word
- `n` = number of items
