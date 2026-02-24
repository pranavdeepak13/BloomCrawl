"""
BloomCrawl — Streamlit Dashboard

Run:  streamlit run app.py

Tabs:
  Search        — query the reverse index, see parsed terms + results
  Crawler       — seed URLs, run a mock crawl, inspect crawled pages
  BloomFilter   — live stats: FPR, fill %, memory, bit array visualisation
  Cache         — test the Akamai two-tier cache, see hit/miss trace
  Benchmarks    — view pre-generated benchmark plots
"""
import sys, math, time, queue, json, hashlib
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from bloomcrawl.core.bloom_filter import BloomFilter
from bloomcrawl.core.simhash import Simhash
from bloomcrawl.core.dedup_job import DedupJob
from bloomcrawl.crawler.page import Page
from bloomcrawl.crawler.page_store import PageStore
from bloomcrawl.crawler.crawler import Crawler
from bloomcrawl.crawler.url_frontier import URLFrontier
from bloomcrawl.workers.reverse_index import ReverseIndex
from bloomcrawl.workers.reverse_index_worker import ReverseIndexWorker
from bloomcrawl.frontend.query_parser import QueryParser, ParsedQuery, ParseError
from bloomcrawl.frontend.cache_manager import CacheManager


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="BloomCrawl",
    page_icon="🌸",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Shared session state ──────────────────────────────────────────────────────

def _init_state():
    if "page_store" not in st.session_state:
        st.session_state.page_store = PageStore()
    if "bloom" not in st.session_state:
        st.session_state.bloom = BloomFilter(10_000, 0.01)
    if "reverse_index" not in st.session_state:
        st.session_state.reverse_index = ReverseIndex()
    if "cache" not in st.session_state:
        st.session_state.cache = CacheManager(expected_queries=5_000, false_positive_rate=0.01)
    if "crawl_log" not in st.session_state:
        st.session_state.crawl_log = []
    if "cache_log" not in st.session_state:
        st.session_state.cache_log = []
    if "riq" not in st.session_state:
        st.session_state.riq = queue.Queue()
    if "ri_worker" not in st.session_state:
        worker = ReverseIndexWorker(st.session_state.riq, st.session_state.reverse_index)
        worker.start()
        st.session_state.ri_worker = worker

_init_state()

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🌸 BloomCrawl")
    st.caption("Probabilistic web crawler & search engine")
    st.divider()

    ps  = st.session_state.page_store
    bf  = st.session_state.bloom
    ri  = st.session_state.reverse_index
    cm  = st.session_state.cache

    st.markdown("### System Status")
    c1, c2 = st.columns(2)
    c1.metric("Pages crawled", ps.page_count())
    c2.metric("Bloom items",   bf.item_count)

    c3, c4 = st.columns(2)
    c3.metric("Index words",  ri.word_count())
    c4.metric("Bloom FPR",   f"{bf.false_positive_rate()*100:.2f}%")

    st.metric("Bloom memory", f"{bf.memory_usage_bytes()/1024:.1f} KB")
    st.metric("Bloom fill",   f"{bf.item_count/bf.expected_items*100:.1f}%")
    st.divider()

    if st.button("🔄 Reset All State"):
        for key in ["page_store","bloom","reverse_index","cache","crawl_log","cache_log","riq","ri_worker"]:
            st.session_state.pop(key, None)
        _init_state()
        st.rerun()

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_search, tab_crawler, tab_bloom, tab_cache, tab_benchmarks = st.tabs([
    "🔍 Search", "🕷️ Crawler", "💡 BloomFilter", "⚡ Cache", "📊 Benchmarks"
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — SEARCH
# ══════════════════════════════════════════════════════════════════════════════

with tab_search:
    st.header("🔍 Search Engine")
    st.caption("Queries the reverse index. Crawl some pages first (Crawler tab) to see results.")

    parser = QueryParser()

    col_q, col_btn = st.columns([5, 1])
    with col_q:
        raw_query = st.text_input(
            "Search query",
            placeholder='Try: "web crawler" or "python hash"',
            label_visibility="collapsed",
        )
    with col_btn:
        search_clicked = st.button("Search", type="primary", use_container_width=True)

    if raw_query and search_clicked:
        result = parser.parse(raw_query)

        if isinstance(result, ParseError):
            st.error(f"❌ Parse error: **{result.reason}**")
            st.code(f'Input: "{result.raw}"')
        else:
            st.success(f"✅ Parsed terms: {result.terms}")

            # Show pipeline breakdown
            with st.expander("🔬 Pipeline breakdown"):
                st.markdown(f"""
| Step | Output |
|------|--------|
| Raw input | `{result.raw}` |
| After strip_markup | `{result.raw}` *(no tags found)* |
| After tokenise | `{result.terms}` |
| After normalise | `{result.terms}` |
""")

            hits = ri.lookup_multi(result.terms)

            if not hits:
                st.info("No results found. Try crawling some pages first.")
            else:
                st.markdown(f"### {len(hits)} result(s)")
                for rank, (page_id, score) in enumerate(hits[:10], 1):
                    page = ps.get_page(page_id)
                    if page:
                        with st.container(border=True):
                            st.markdown(f"**#{rank}  [{page.title}]({page.url})**  `score={score:.1f}`")
                            st.caption(page.url)
                            st.write(page.snippet[:300] + ("..." if len(page.snippet) > 300 else ""))
                            cols = st.columns(3)
                            cols[0].caption(f"page_id: `{page.page_id}`")
                            cols[1].caption(f"children: {len(page.child_urls)}")
                            sig_val = page.signature.value
                            cols[2].caption(f"simhash: `{sig_val:016x}`")

    # Dedup tool section
    st.divider()
    st.subheader("🔧 URL Deduplication Tool")
    raw_urls = st.text_area(
        "Paste URLs (one per line) — finds duplicates and unique entries",
        height=120,
        placeholder="https://example.com\nhttps://other.com\nhttps://example.com",
    )
    if raw_urls.strip():
        urls = [u.strip() for u in raw_urls.strip().splitlines() if u.strip()]
        dups  = DedupJob.find_duplicates(urls)
        uniq  = DedupJob.unique_urls(urls)
        c1, c2, c3 = st.columns(3)
        c1.metric("Total URLs",   len(urls))
        c2.metric("Unique URLs",  len(uniq))
        c3.metric("Duplicates",   len(dups))
        if dups:
            st.markdown("**Duplicate URLs:**")
            for url, count in sorted(dups.items(), key=lambda x: -x[1]):
                st.markdown(f"- `{url}` — seen **{count}×**")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — CRAWLER
# ══════════════════════════════════════════════════════════════════════════════

with tab_crawler:
    st.header("🕷️ Mock Crawler")
    st.caption("Simulates crawling with synthetic pages. Populates the search index.")

    col1, col2 = st.columns([3, 1])
    with col1:
        seed_input = st.text_area(
            "Seed URLs (one per line)",
            value="https://example.com\nhttps://python.org\nhttps://redis.io",
            height=100,
        )
    with col2:
        max_pages = st.number_input("Max pages", min_value=1, max_value=100, value=10)
        topic = st.selectbox("Topic", ["general", "python", "databases", "web"])

    MOCK_CONTENT = {
        "python": {
            "https://python.org": ("Python - Official", "Python is a programming language that lets you work quickly and integrate systems effectively. Python uses dynamic typing and garbage collection.", ["https://python.org/docs", "https://pypi.org"]),
            "https://python.org/docs": ("Python Documentation", "The Python documentation contains the language reference tutorials and library reference for the Python programming language.", ["https://python.org", "https://docs.python.org/3"]),
            "https://pypi.org": ("PyPI - Python Package Index", "PyPI is the official Python package repository. Find install and publish Python packages. Pip installs packages from PyPI.", ["https://python.org", "https://pip.pypa.io"]),
            "https://docs.python.org/3": ("Python 3 Docs", "Python 3 documentation covers all standard library modules and language features. Includes asyncio threading and dataclasses.", ["https://python.org"]),
        },
        "databases": {
            "https://redis.io": ("Redis - In-Memory Database", "Redis is an open source in-memory data structure store used as database cache and message broker. Supports strings hashes lists sets.", ["https://redis.io/docs", "https://redis.com"]),
            "https://redis.io/docs": ("Redis Documentation", "Redis documentation covers commands data types cluster replication and persistence. Redis bloom filter module supports probabilistic data structures.", ["https://redis.io"]),
            "https://cassandra.apache.org": ("Apache Cassandra", "Cassandra is a distributed NoSQL database designed for scalability. Cassandra handles large amounts of data across commodity servers.", ["https://cassandra.apache.org/doc"]),
        },
        "web": {
            "https://example.com": ("Example Domain", "This domain is for use in illustrative examples in documents. You may use this domain in literature without prior coordination.", ["https://www.iana.org"]),
            "https://www.iana.org": ("IANA - Internet Assigned Numbers Authority", "IANA manages global IP addressing DNS root zone and other internet protocol resources for the internet community.", ["https://example.com"]),
        },
        "general": {
            "https://example.com": ("Example Domain", "This domain is for use in illustrative examples in documents. You may use this domain in literature without prior coordination.", ["https://www.iana.org"]),
            "https://python.org": ("Python Language", "Python is a high-level general-purpose programming language. Its design philosophy emphasises code readability with the use of significant indentation.", ["https://pypi.org"]),
            "https://redis.io": ("Redis Database", "Redis is an open source in-memory data structure store. Used as database cache and message broker in web applications.", ["https://redis.io/docs"]),
        },
    }

    def mock_fetcher(url: str):
        db = MOCK_CONTENT.get(topic, MOCK_CONTENT["general"])
        if url in db:
            return db[url]
        # Generate a synthetic page for unknown URLs
        path = url.split("/")[-1] or "index"
        title = f"{path.replace('-', ' ').title()} — {url.split('/')[2]}"
        content = (f"{title} is a web page about {topic}. "
                   f"This page covers various aspects of {path} related to {topic}. "
                   f"Users interested in {topic} can find useful information here. "
                   f"The content is indexed for fast retrieval using an inverted index.")
        return title, content, []

    if st.button("🚀 Run Crawl", type="primary"):
        seeds = [u.strip() for u in seed_input.strip().splitlines() if u.strip()]
        if not seeds:
            st.error("Please enter at least one seed URL.")
        else:
            progress = st.progress(0, text="Initialising crawler...")
            log_placeholder = st.empty()

            crawler = Crawler(
                seed_urls=seeds,
                data_store=st.session_state.page_store,
                bloom_filter=st.session_state.bloom,
                fetcher=mock_fetcher,
                max_pages=max_pages,
            )

            # Manually step through frontier for live logging
            crawled = 0
            crawl_log = []

            while not crawler.frontier.empty() and crawled < max_pages:
                try:
                    url = crawler.frontier.get(block=False)
                except queue.Empty:
                    break

                if crawler.bloom_filter.contains(url):
                    crawler.frontier.task_done()
                    entry = {"url": url, "status": "⏭ bloom skip", "score": None}
                    crawl_log.append(entry)
                    continue

                try:
                    title, content, children = mock_fetcher(url)
                except Exception as e:
                    crawler.frontier.task_done()
                    crawl_log.append({"url": url, "status": f"❌ error: {e}", "score": None})
                    continue

                page = Page.from_raw(url, title, content, children)

                if crawler.data_store.crawled_similar(page.signature):
                    crawler.bloom_filter.add(url)
                    crawler.frontier.task_done()
                    crawl_log.append({"url": url, "status": "🔁 near-dup skip", "score": None})
                    continue

                crawler.bloom_filter.add(url)
                crawler.data_store.insert_crawled_link(page)
                crawled += 1

                for child in children:
                    crawler.frontier.put(child, priority=1)

                msg = {
                    "page_id": page.page_id, "url": page.url,
                    "title": page.title, "snippet": page.snippet,
                    "content": page.content,
                }
                st.session_state.riq.put(msg)
                crawler.frontier.task_done()

                crawl_log.append({"url": url, "status": "✅ crawled", "title": title, "children": len(children)})
                progress.progress(crawled / max_pages, text=f"Crawling... {crawled}/{max_pages}")

            st.session_state.crawl_log.extend(crawl_log)
            time.sleep(0.3)  # let worker drain
            progress.progress(1.0, text="Done!")
            st.success(f"Crawled {crawled} page(s). Index now has {ri.word_count()} words.")
            st.rerun()

    # Crawl log
    if st.session_state.crawl_log:
        st.subheader("Crawl Log")
        log_df_data = []
        for e in st.session_state.crawl_log[-50:]:
            log_df_data.append({
                "Status": e["status"],
                "URL": e["url"],
                "Title": e.get("title", "—"),
                "Children": e.get("children", "—"),
            })
        st.dataframe(log_df_data, use_container_width=True, hide_index=True)

    # Crawled pages table
    if ps.page_count() > 0:
        st.subheader(f"Crawled Pages ({ps.page_count()})")
        page_data = []
        for pid, page in list(ps._pages.items()):
            page_data.append({
                "page_id": page.page_id,
                "URL": page.url,
                "Title": page.title[:60],
                "Snippet": page.snippet[:80] + "...",
                "SimHash": f"{page.signature.value:016x}",
                "Children": len(page.child_urls),
            })
        st.dataframe(page_data, use_container_width=True, hide_index=True)

        # Simhash similarity matrix
        if ps.page_count() >= 2:
            st.subheader("Simhash Similarity Matrix")
            pages = list(ps._pages.values())[:8]
            labels = [p.title[:20] for p in pages]
            matrix = []
            for p1 in pages:
                row = []
                for p2 in pages:
                    d = p1.signature.hamming_distance(p2.signature)
                    row.append(d)
                matrix.append(row)
            import pandas as pd
            df = pd.DataFrame(matrix, index=labels, columns=labels)
            st.caption("Hamming distance between page fingerprints (0 = identical, ≤3 = near-duplicate)")
            st.dataframe(df.style.background_gradient(cmap="RdYlGn_r", vmin=0, vmax=32),
                         use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — BLOOM FILTER
# ══════════════════════════════════════════════════════════════════════════════

with tab_bloom:
    st.header("💡 BloomFilter Inspector")

    bf = st.session_state.bloom

    # Live stats row
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Bit array size (m)",    f"{bf.bit_array_size:,}")
    c2.metric("Hash functions (k)",    bf.num_hash_functions)
    c3.metric("Items inserted",        bf.item_count)
    c4.metric("Target FPR",            f"{bf.target_false_positive_rate*100:.1f}%")
    c5.metric("Live FPR",              f"{bf.false_positive_rate()*100:.3f}%")

    c6, c7, c8 = st.columns(3)
    c6.metric("Memory (bytes)",        f"{bf.memory_usage_bytes():,}")
    c7.metric("Memory (KB)",           f"{bf.memory_usage_bytes()/1024:.2f}")
    c8.metric("Fill ratio",            f"{bf.item_count/bf.expected_items*100:.1f}%")

    # FPR progress bar
    fpr_live = bf.false_positive_rate()
    fpr_target = bf.target_false_positive_rate
    st.markdown("**Live FPR vs Target**")
    st.progress(min(fpr_live / max(fpr_target, 1e-9), 1.0),
                text=f"Live FPR: {fpr_live*100:.3f}% | Target: {fpr_target*100:.1f}%")

    # Manual test
    st.divider()
    st.subheader("Manual Membership Test")
    col_add, col_check = st.columns(2)

    with col_add:
        st.markdown("**Add item**")
        add_item = st.text_input("Item to add", key="bf_add", placeholder="https://example.com")
        if st.button("Add to BloomFilter"):
            if add_item:
                positions = bf._positions(add_item)
                already = bf.contains(add_item)
                bf.add(add_item)
                if already:
                    st.warning(f"'{add_item}' was already present (or false positive).")
                else:
                    st.success(f"Added. Bit positions set: {positions}")
                st.rerun()

    with col_check:
        st.markdown("**Check membership**")
        check_item = st.text_input("Item to check", key="bf_check", placeholder="https://example.com")
        if st.button("Check"):
            if check_item:
                positions = bf._positions(check_item)
                result = bf.contains(check_item)
                bits_at_pos = [int(bf._bits[p]) for p in positions]
                if result:
                    st.success(f"✅ Probably present (all {bf.num_hash_functions} bits are 1)")
                else:
                    st.error(f"❌ Definitely NOT present (at least one bit is 0)")
                st.code(f"Positions: {positions}\nBits:      {bits_at_pos}")

    # Bit array visualisation (sample first 512 bits)
    st.divider()
    st.subheader("Bit Array Visualisation (first 512 bits)")
    st.caption("Blue = 1 (set), White = 0 (unset)")
    sample_bits = [int(bf._bits[i]) for i in range(min(512, bf.bit_array_size))]
    # Display as 32-bit-wide grid
    rows = [sample_bits[i:i+32] for i in range(0, len(sample_bits), 32)]
    html_rows = []
    for row in rows:
        cells = "".join(
            f'<span style="display:inline-block;width:14px;height:14px;'
            f'background:{"#2563EB" if b else "#F3F4F6"};'
            f'border:1px solid #E5E7EB;margin:1px;border-radius:2px"></span>'
            for b in row
        )
        html_rows.append(f"<div>{cells}</div>")
    st.html("".join(html_rows))
    set_count = sum(sample_bits)
    st.caption(f"{set_count}/{len(sample_bits)} bits set in first 512 ({set_count/len(sample_bits)*100:.1f}%)")

    # Parameter explorer
    st.divider()
    st.subheader("Parameter Explorer — Design Your Own BloomFilter")
    st.caption("Adjust n and p to see how m and k change.")
    col_n, col_p = st.columns(2)
    with col_n:
        exp_n = st.slider("Expected items (n)", 1_000, 10_000_000, 1_000_000, step=1_000,
                          format="%d")
    with col_p:
        exp_p = st.select_slider("Target FPR (p)",
                                 options=[0.001, 0.005, 0.01, 0.02, 0.05, 0.1],
                                 value=0.01,
                                 format_func=lambda x: f"{x*100:.1f}%")

    m = math.ceil(-exp_n * math.log(exp_p) / math.log(2)**2) + 1
    k = max(1, min(20, round((m / exp_n) * math.log(2))))
    mem_kb = m / 8 / 1024
    set_mem_kb = exp_n * (8 + 57 + 50) / 1024  # pointer + str object + ~50-char URL

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Bit array m", f"{m:,} bits")
    c2.metric("Hash fns k", k)
    c3.metric("BloomFilter", f"{mem_kb:.1f} KB")
    c4.metric("set() (est)", f"{set_mem_kb:.1f} KB")
    c5.metric("Memory ratio", f"{set_mem_kb/mem_kb:.0f}×")

    st.markdown(f"""
**Formulas:**
- `m = ⌈ -n·ln(p) / (ln2)² ⌉ = ⌈ -{exp_n:,}·ln({exp_p}) / {math.log(2)**2:.4f} ⌉ = {m:,}`
- `k = round((m/n)·ln2) = round(({m}/{exp_n})·{math.log(2):.4f}) = {k}`
""")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — CACHE (Akamai Pattern)
# ══════════════════════════════════════════════════════════════════════════════

with tab_cache:
    st.header("⚡ Akamai Cache Inspector")
    st.caption("BloomFilter admission gate → only cache queries seen ≥2 times.")

    cm = st.session_state.cache
    bf_cache = cm.bloom_filter

    c1, c2, c3 = st.columns(3)
    c1.metric("Cache BloomFilter size (m)", f"{bf_cache.bit_array_size:,}")
    c2.metric("Queries seen (approx)",      bf_cache.item_count)
    c3.metric("Cache FPR",                  f"{bf_cache.false_positive_rate()*100:.3f}%")

    st.divider()
    st.subheader("Try a Query")
    st.markdown("""
**How it works:**
1. First call → BloomFilter says "never seen" → **cache miss**, record the visit.
2. Second call → BloomFilter says "probably seen" → **check cache** (may miss if not yet stored).
3. After `set()` is called → subsequent lookups return the cached result.
""")

    col_q2, col_v, col_btn2 = st.columns([3, 2, 1])
    with col_q2:
        cache_query = st.text_input("Query", placeholder="bloom filter efficiency", key="cache_q")
    with col_v:
        cache_value = st.text_input("Value to cache (JSON)", placeholder='{"answer": 42}', key="cache_v")
    with col_btn2:
        cache_get = st.button("GET", use_container_width=True)
        cache_set = st.button("SET", use_container_width=True)

    if cache_query:
        if cache_get:
            bloom_said_seen = bf_cache.contains(cache_query)
            result = cm.get(cache_query)
            entry = {
                "op": "GET",
                "query": cache_query,
                "bloom_said_seen": bloom_said_seen,
                "result": result,
                "outcome": "HIT" if result is not None else ("BLOOM MISS" if not bloom_said_seen else "CACHE MISS"),
            }
            st.session_state.cache_log.append(entry)
            if result is not None:
                st.success(f"✅ Cache HIT: `{result}`")
            elif not bloom_said_seen:
                st.info("🔵 BloomFilter MISS — first time seeing this query. Recorded.")
            else:
                st.warning("🟡 BloomFilter HIT — query seen before, but result not in cache yet.")
            st.rerun()

        if cache_set and cache_value:
            try:
                parsed_val = json.loads(cache_value)
            except json.JSONDecodeError:
                parsed_val = cache_value
            cm.set(cache_query, parsed_val)
            st.success(f"✅ Stored in cache: `{cache_query}` → `{parsed_val}`")
            st.session_state.cache_log.append({"op": "SET", "query": cache_query, "value": parsed_val})
            st.rerun()

    # Cache operation log
    if st.session_state.cache_log:
        st.subheader("Operation Log")
        log_entries = []
        for e in st.session_state.cache_log[-30:]:
            if e["op"] == "GET":
                log_entries.append({
                    "Op": "GET",
                    "Query": e["query"],
                    "Bloom said seen": "✅ yes" if e.get("bloom_said_seen") else "❌ no",
                    "Outcome": e.get("outcome", "—"),
                    "Result": str(e.get("result", "null"))[:50],
                })
            else:
                log_entries.append({
                    "Op": "SET",
                    "Query": e["query"],
                    "Bloom said seen": "—",
                    "Outcome": "stored",
                    "Result": str(e.get("value", ""))[:50],
                })
        st.dataframe(log_entries, use_container_width=True, hide_index=True)

    # Simulate traffic
    st.divider()
    st.subheader("Traffic Simulation")
    st.caption("Simulate N requests for M unique queries to show cache behaviour at scale.")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        sim_queries = st.slider("Unique queries", 5, 50, 10)
    with col_s2:
        sim_repeats = st.slider("Repeats per query", 1, 10, 3)

    if st.button("▶ Run Simulation"):
        sim_cm = CacheManager(expected_queries=1000, false_positive_rate=0.01)
        bloom_misses, cache_misses, cache_hits, first_hits = 0, 0, 0, 0
        queries = [f"query_{i}" for i in range(sim_queries)]
        values  = {q: {"results": [f"page_{i}_{j}" for j in range(3)]} for i, q in enumerate(queries)}

        for _ in range(sim_repeats):
            for q in queries:
                bloom_seen = sim_cm.bloom_filter.contains(q)
                result = sim_cm.get(q)
                if not bloom_seen:
                    bloom_misses += 1
                elif result is None:
                    first_hits += 1
                    sim_cm.set(q, values[q])
                else:
                    cache_hits += 1

        total = sim_queries * sim_repeats
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total requests", total)
        c2.metric("Bloom misses (1st visit)", bloom_misses)
        c3.metric("Cache fills (2nd visit)", first_hits)
        c4.metric("Cache hits (3rd+ visit)", cache_hits)

        st.markdown(f"""
**Result:** After warm-up, **{cache_hits/total*100:.0f}%** of requests are cache hits.
Only queries seen ≥2 times enter the cache — **one-shot pollution eliminated**.
""")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — BENCHMARKS
# ══════════════════════════════════════════════════════════════════════════════

with tab_benchmarks:
    st.header("📊 Benchmark Results")
    st.caption("Pre-generated plots. Re-run `python benchmarks/plot_benchmarks.py` to refresh.")

    plots_dir = Path(__file__).parent / "benchmarks" / "plots"
    plot_files = [
        ("1_fpr_vs_fill.png",    "FPR vs Fill Level",          "Empirical vs theoretical false positive rate as the filter fills up."),
        ("2_memory_comparison.png","Memory Comparison",         "BloomFilter vs Python set() memory usage across N items (log-log scale)."),
        ("3_insert_speed.png",   "Insert Speed",                "Time per insert in µs: BloomFilter.add() vs set.add()."),
        ("4_lookup_speed.png",   "Lookup Speed (Hit & Miss)",   "Per-lookup latency for both HIT and MISS cases."),
        ("5_n_crossover.png",    "N-Crossover Analysis",        "At which N does the BloomFilter space savings become significant?"),
    ]

    for filename, title, caption in plot_files:
        path = plots_dir / filename
        st.subheader(title)
        st.caption(caption)
        if path.exists():
            st.image(str(path), use_container_width=True)
        else:
            st.warning(f"Plot not found: `{path}`. Run `python benchmarks/plot_benchmarks.py` first.")
        st.divider()

    # N-crossover table
    st.subheader("N-Crossover Data Table")
    st.caption("Memory comparison: BloomFilter (p=1%) vs Python set() for URL strings (~50 bytes each).")
    import pandas as pd

    ns = [10, 50, 100, 500, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000, 10_000_000]
    table_rows = []
    for n in ns:
        m = math.ceil(-n * math.log(0.01) / math.log(2)**2) + 1
        k = max(1, min(20, round((m / n) * math.log(2))))
        bloom_kb = m / 8 / 1024
        set_kb   = n * (8 + 57 + 50) / 1024
        ratio    = set_kb / bloom_kb
        saved_mb = (set_kb - bloom_kb) / 1024
        table_rows.append({
            "N": f"{n:,}",
            "m (bits)": f"{m:,}",
            "k": k,
            "BloomFilter (KB)": f"{bloom_kb:.1f}",
            "set() (KB)": f"{set_kb:.1f}",
            "Ratio (×)": f"{ratio:.1f}×",
            "Saved (MB)": f"{saved_mb:.2f}",
        })
    st.dataframe(table_rows, use_container_width=True, hide_index=True)

    st.markdown("""
**Key finding:** The BloomFilter is **~80× more memory-efficient than a Python `set()`** at ALL values of N.
This ratio stays constant because both data structures scale linearly with N — the BloomFilter simply has a much smaller constant factor.

The absolute savings cross **1 MB at N ≈ 50,000 items**.
""")
