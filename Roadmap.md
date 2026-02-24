# BloomCrawl — Project Roadmap & Scope Improvements

> From learning project to production-grade distributed web intelligence platform.

---

## Current State (v1.0 — MVP)

| Component | Current | Production Gap |
|-----------|---------|----------------|
| BloomFilter | In-process bitarray | Not shared across workers |
| Simhash dedup | O(n) linear scan | Won't scale past ~50k pages |
| Crawler | Single-threaded, synchronous | No concurrency, no rate limiting |
| ReverseIndex | In-memory dict | Lost on restart, no ranking |
| Storage | Python dicts | Not persistent, not distributed |
| Frontend | Streamlit (local) | No authentication, no deployment |
| Fetcher | urllib (basic) | No robots.txt, no JS rendering |

---

## Phase 1 — Distributed Core (High Impact, Moderate Effort)

### 1.1 Redis-Backed Distributed Bloom Filter

**Problem:** The current BloomFilter lives in one process. If you run 10 crawler workers, each has its own BloomFilter — they can't share "seen URL" state, leading to duplicate fetches.

**Solution:** Back the BloomFilter with Redis bit operations.

```
Redis SETBIT key offset 1       ← bloom.add(url)
Redis GETBIT key offset         ← bloom.contains(url)
```

Redis `BITFIELD` and `BITPOS` commands operate on a single key's bit array. A 9.58MB bit array for 1M URLs fits comfortably in Redis memory.

**Implementation Plan:**
- Add `RedisBloomFilter` class implementing the same `add()/contains()` interface.
- Use pipeline batching: send all k `GETBIT` commands in one `PIPELINE` call (reduces RTT from k to 1).
- Use `BITPOS` for bulk bloom membership checks.

**Benefit:** Any number of crawler workers share a single authoritative "seen" set. Enables horizontal scaling.

---

### 1.2 Simhash Band Lookup (O(1) Near-Duplicate Detection)

**Problem:** `PageStore.crawled_similar()` is O(n) — scans every stored page. At 1M pages it becomes the bottleneck.

**Solution:** Simhash Locality-Sensitive Hashing (LSH) via prefix bands.

**Theory:**
Divide the 64-bit fingerprint into B bands of R bits each (B × R = 64).
Two fingerprints with Hamming distance d ≤ threshold will agree on at least one band with probability:
```
P(at least one band matches) = 1 - (1 - (1 - d/64)^R)^B
```
Choose B and R to make this probability ≥ 0.99 for d ≤ 3.
For B=4 bands of R=16 bits: threshold d=3 → detection probability ≈ 99.6%.

**Implementation Plan:**
- For each inserted page, store its 4 band values in 4 dicts: `band_k → set(page_ids)`.
- Lookup: compute 4 band values of query → check each dict → union of matching page IDs → verify Hamming distance exactly.
- O(1) average case (hash table lookup), O(candidates) verification.

---

### 1.3 Counting Bloom Filter (Deletions)

**Problem:** Standard BloomFilter can't delete items (bits are shared between items — clearing a bit breaks other items).

**Solution:** Replace each bit with a small counter (4-bit or 8-bit integer).

```
add(x):     increment all k positions
remove(x):  decrement all k positions
contains(x): check all k positions > 0
```

**Use cases:**
- **URL expiry**: Remove URLs from the seen-set when their TTL expires so they can be re-crawled.
- **Cache eviction**: Remove entries from `CacheManager` bloom when the cached result is invalidated.
- **Dynamic content**: News sites change every hour — you want to re-crawl them.

**Memory cost:** 4× more memory than a standard Bloom Filter (4-bit counters vs 1-bit). Still 2× more efficient than a Python `set`.

---

### 1.4 Scalable Rotating Bloom Filter

**Problem:** A Bloom Filter's FPR increases monotonically as it fills. A crawler that runs for months will eventually have a near-useless BloomFilter (FPR → 1).

**Solution:** Sliding-window bloom filter using two alternating filters.

```
Active filter (current window): writes + reads
Backup filter (previous window): reads only
Rotation: when active filter reaches 80% capacity,
          make active → backup, create new active.
```

Any `contains()` check reads both. Any `add()` writes to active only. After rotation, URLs from 2+ windows ago are effectively "forgotten" and can be re-crawled. This models the real web: content from 6 months ago should be re-fetched.

---

## Phase 2 — Crawl Infrastructure (High Impact, High Effort)

### 2.1 Politeness: robots.txt + Rate Limiting

**Problem:** A crawler that doesn't respect `robots.txt` violates web standards and can get IP-banned.

**Implementation Plan:**
- Fetch and cache `https://domain.com/robots.txt` before crawling any page on that domain.
- Parse `Disallow:` and `Crawl-delay:` directives.
- Per-domain rate limiter using token bucket algorithm:
  ```
  tokens += min(max_rate, tokens + elapsed × refill_rate)
  if tokens >= 1: crawl and deduct 1 token
  else: wait
  ```
- Respect `Crawl-delay:` value from robots.txt.

**Libraries:** `urllib.robotparser` (stdlib), `aiohttp` for async HTTP.

---

### 2.2 Async Crawling (aiohttp + asyncio)

**Problem:** Current `urllib.request.urlopen()` is blocking. While waiting for network I/O, the CPU is idle.

**Solution:** Replace with `asyncio` + `aiohttp`. A single-threaded event loop can handle thousands of concurrent in-flight HTTP requests.

```python
async def _fetch_async(url: str, session: aiohttp.ClientSession):
    async with session.get(url, timeout=10) as resp:
        html = await resp.text(errors="ignore")
    ...
```

**Expected throughput improvement:** 10–100× for I/O-bound crawling (network latency dominates).

---

### 2.3 JavaScript Rendering (Playwright / Selenium)

**Problem:** Many modern web pages are Single Page Applications (React, Vue) — they return mostly empty HTML that is populated by JavaScript. Our current `urllib` fetcher gets the empty shell, not the rendered content.

**Solution:** Use Playwright (async, Chromium-based) for JS-heavy pages:
```python
from playwright.async_api import async_playwright
async with async_playwright() as p:
    browser = await p.chromium.launch()
    page = await browser.new_page()
    await page.goto(url)
    await page.wait_for_load_state("networkidle")
    html = await page.content()
```

**Cost:** Each Playwright fetch is ~10× slower than a raw HTTP request. Use heuristics to decide when to invoke the full browser (e.g., check for `<div id="root">` with minimal content).

---

### 2.4 Distributed Work Queue (Celery + Redis/RabbitMQ)

**Problem:** Python's `queue.Queue` is in-process only. Can't distribute work across multiple machines.

**Solution:** Replace `URLFrontier` with Celery tasks backed by Redis or RabbitMQ.

```python
@celery_app.task
def crawl_url(url: str, priority: int) -> None:
    ...
```

This enables horizontal scaling: spin up 100 Celery workers across 10 machines, all consuming from the same queue.

---

## Phase 3 — Search Quality (Moderate Impact, Moderate Effort)

### 3.1 TF-IDF Scoring

**Problem:** Current scoring is binary (title=2, body=1). Two pages with the same word count as the same relevance, even if one word appears once and another appears 50 times.

**Solution:** TF-IDF (Term Frequency – Inverse Document Frequency):

```
TF(t, d)    = count(t in d) / total_words(d)
IDF(t)      = log(total_docs / docs_containing_t)
TF-IDF(t,d) = TF(t,d) × IDF(t)
```

TF rewards documents where the query term is frequent. IDF penalises terms that appear in many documents (common words like "the" get low IDF even if they appear often). Together they identify documents where the term is both frequent AND discriminative.

**Implementation:** Store `(page_id, tf_idf_score)` tuples in the `ReverseIndex` instead of binary scores.

---

### 3.2 BM25 Scoring (Better than TF-IDF)

**Problem:** TF-IDF has no length normalisation — a 10,000-word document will score higher than a 100-word document just by having more word occurrences.

**Solution:** BM25 (Best Match 25) — the standard ranking function used by Elasticsearch, Lucene, and Solr:

```
BM25(t, d) = IDF(t) × (tf(t,d) × (k1+1)) / (tf(t,d) + k1×(1 - b + b×|d|/avgdl))
```

Where:
- `k1 ≈ 1.5`: term frequency saturation (diminishing returns for extra occurrences)
- `b ≈ 0.75`: length normalisation factor
- `|d|`: document length in words
- `avgdl`: average document length across corpus

BM25 consistently outperforms TF-IDF in information retrieval benchmarks.

---

### 3.3 PageRank

**Problem:** All crawled pages are treated as equally important. A page linked by 10,000 other pages should rank higher than a page nobody links to.

**Solution:** PageRank (Larry Page & Sergey Brin, 1998):
```
PR(u) = (1-d)/N + d × Σ PR(v)/out_degree(v)   for all v → u
```
Where `d = 0.85` is the damping factor (probability of following a link vs random jump).

**Iterative computation** (power iteration):
1. Initialise all pages PR = 1/N.
2. Propagate: each page distributes its PR evenly across its outbound links.
3. Repeat until convergence (change < ε).

Typically converges in 50–100 iterations. Final ranking = TF-IDF × PageRank.

---

### 3.4 Phrase Search and Boolean Operators

**Problem:** Searching "new york" returns pages with "new" and "york" anywhere, not necessarily adjacent.

**Solution:**
- **Positional index:** Store `word → [(page_id, [position1, position2, ...])]`. Phrase search checks if positions are consecutive.
- **Boolean operators:** Parse `AND`, `OR`, `NOT` in queries.
  ```
  "python AND web AND NOT java" → intersect("python", "web") - "java"
  ```
- **Proximity search:** `"python" NEAR "web"` → pages where the terms appear within N words of each other.

---

## Phase 4 — Data Layer (Production Readiness)

### 4.1 Replace In-Memory Dicts with Persistent Storage

| Current | Production Replacement | Why |
|---------|----------------------|-----|
| `PageStore._pages` dict | Cassandra or DynamoDB | Distributed, persistent, fault-tolerant |
| `ReverseIndex._idx` dict | Elasticsearch or OpenSearch | Full-text search, TF-IDF, aggregations built-in |
| `CacheManager._local` dict | Redis with TTL | Distributed, persistent, built-in expiry |
| `URLFrontier` PriorityQueue | Redis Sorted Sets | `ZADD`/`ZPOPMIN` → O(log n) priority queue |

---

### 4.2 Redis Sorted Set as Priority Queue

Replace `queue.PriorityQueue` with Redis:
```
ZADD frontier <priority> <url>    ← put(url, priority)
ZPOPMIN frontier 1                ← get() → lowest-priority url
ZCARD frontier                    ← size()
```
This is persisted, shared across workers, and survives process restarts.

---

### 4.3 Content Store (S3 / Object Storage)

Store raw HTML and extracted text in S3/GCS/MinIO:
- Key: `pages/{page_id}/raw.html`, `pages/{page_id}/content.txt`
- Benefits: unlimited size, cheap storage, re-processable (can re-run extraction)
- Index metadata (title, snippet, URL, scores) in Cassandra/DynamoDB (fast key-value lookups)
- Serves as source of truth for re-indexing after algorithm changes

---

## Phase 5 — Observability & Operations

### 5.1 Metrics (Prometheus + Grafana)

Key metrics to expose:
```
bloomcrawl_urls_crawled_total         Counter
bloomcrawl_bloom_filter_fpr           Gauge
bloomcrawl_dedup_near_duplicates      Counter
bloomcrawl_frontier_size              Gauge
bloomcrawl_crawl_latency_seconds      Histogram (p50, p95, p99)
bloomcrawl_cache_hit_rate             Gauge
bloomcrawl_reverse_index_word_count   Gauge
```

Use `prometheus_client` Python library. Expose `/metrics` endpoint. Grafana dashboard shows live system health.

---

### 5.2 Structured Logging (structlog)

Replace `print()` statements with structured JSON logs:
```python
import structlog
log = structlog.get_logger()
log.info("page_crawled", url=url, page_id=page.page_id,
         latency_ms=elapsed, child_count=len(children))
```
Structured logs are machine-parseable → queryable in Elasticsearch/Splunk/Datadog.

---

### 5.3 Distributed Tracing (OpenTelemetry)

Trace the full lifecycle of a URL: enqueued → fetched → deduplicated → indexed.
Visualise in Jaeger or Zipkin. Identify latency bottlenecks across async boundaries.

---

## Phase 6 — Machine Learning Extensions

### 6.1 Content Classification

Train a classifier to tag crawled pages by topic (technology, sports, politics, etc.):
- **Input:** page title + first 512 tokens of content.
- **Model:** DistilBERT fine-tuned on AG News or similar dataset.
- **Output:** `{label: "technology", confidence: 0.94}` stored with the page.
- **Search enhancement:** filter results by topic category.

---

### 6.2 Semantic Search (Vector Embeddings)

Replace keyword matching with semantic similarity:
- **Embed** each page using `sentence-transformers` (produces 384-dim float vector).
- **Store** vectors in a vector database (Pinecone, Weaviate, Qdrant, or pgvector).
- **Query:** embed the search query → find k-nearest neighbours by cosine similarity.
- **Hybrid search:** combine BM25 (lexical) + vector (semantic) scores.

This enables: "find pages about fast sorting" to return results about QuickSort even if they never use the word "fast".

---

### 6.3 Automatic Snippet Generation (Extractive Summarisation)

Instead of returning `content[:200]` as the snippet, extract the most relevant sentence:
- Score each sentence by: overlap with query terms × sentence position weight.
- Return the highest-scoring sentence as the snippet.
- Tools: `sumy`, `nltk`, or a lightweight BERT extractive model.

---

## Development Milestones

```
v1.0  ✅  In-process MVP (current)
v1.1  →   Redis-backed BloomFilter + distributed crawling
v1.2  →   robots.txt + async fetching (aiohttp)
v1.3  →   TF-IDF scoring + phrase search
v2.0  →   Persistent storage (Cassandra + Elasticsearch)
v2.1  →   PageRank + BM25
v2.2  →   Prometheus metrics + structured logging
v3.0  →   Vector search + content classification
v3.1  →   Full deployment (Docker Compose → Kubernetes)
```

---

## Quick Wins (Can Be Done Now)

These improvements require minimal changes to the current codebase:

1. **URL normalisation**: Canonicalise URLs before BloomFilter insertion. `http://example.com`, `http://example.com/`, `HTTP://EXAMPLE.COM` are the same page. Use `urllib.parse.urlparse` + lowercase scheme/host + strip trailing slash.

2. **Domain-level dedup**: Don't crawl more than N pages per domain (configurable). Prevents one site from flooding the entire frontier.

3. **Content-type filtering**: Only crawl `text/html` pages. Skip PDFs, images, videos. Check `Content-Type` header before full download.

4. **Persistent BloomFilter**: Serialise `bitarray` to disk on shutdown, reload on startup. `bitarray` supports `tofile()`/`fromfile()`. Zero-cost restart without losing seen-URL state.

5. **Query suggestion**: When `lookup_multi()` returns no results, suggest the closest matching term by edit distance (Levenshtein distance ≤ 2). "Did you mean: python?"
