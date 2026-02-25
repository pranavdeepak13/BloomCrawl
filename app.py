"""
BloomCrawl — Streamlit Dashboard
Run: streamlit run app.py

Tabs: Search | Crawler | BloomFilter | Cache | Benchmarks
"""
import sys
sys.dont_write_bytecode = True

import math, time, queue, json, urllib.parse
from pathlib import Path
from collections import Counter

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from bloomcrawl.core.bloom_filter import BloomFilter
from bloomcrawl.core.dedup_job import DedupJob
from bloomcrawl.crawler.page import Page
from bloomcrawl.crawler.page_store import PageStore
from bloomcrawl.crawler.crawler import Crawler, _fetch
from bloomcrawl.workers.reverse_index import ReverseIndex
from bloomcrawl.workers.reverse_index_worker import ReverseIndexWorker
from bloomcrawl.frontend.query_parser import QueryParser, ParsedQuery, ParseError
from bloomcrawl.frontend.cache_manager import CacheManager


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="BloomCrawl",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state ─────────────────────────────────────────────────────────────

def _init_state() -> None:
    if "page_store" not in st.session_state:
        st.session_state.page_store = PageStore()
    if "bloom" not in st.session_state:
        st.session_state.bloom = BloomFilter(50_000, 0.01)
    if "reverse_index" not in st.session_state:
        st.session_state.reverse_index = ReverseIndex()
    if "cache" not in st.session_state:
        st.session_state.cache = CacheManager(expected_queries=5_000, false_positive_rate=0.01)
    if "crawl_log" not in st.session_state:
        st.session_state.crawl_log: list[dict] = []
    if "cache_log" not in st.session_state:
        st.session_state.cache_log: list[dict] = []
    if "riq" not in st.session_state:
        st.session_state.riq = queue.Queue()
    if "ri_worker" not in st.session_state:
        w = ReverseIndexWorker(st.session_state.riq, st.session_state.reverse_index)
        w.start()
        st.session_state.ri_worker = w

_init_state()

ps = st.session_state.page_store
bf = st.session_state.bloom
ri = st.session_state.reverse_index
cm = st.session_state.cache

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("BloomCrawl")
    st.caption("Probabilistic web crawler and search engine")
    st.divider()

    st.markdown("**System Status**")
    c1, c2 = st.columns(2)
    c1.metric("Pages crawled", ps.page_count())
    c2.metric("Bloom items",   bf.item_count)
    c3, c4 = st.columns(2)
    c3.metric("Index words",  ri.word_count())
    c4.metric("Live FPR",     f"{bf.false_positive_rate()*100:.2f}%")
    st.metric("Bloom memory", f"{bf.memory_usage_bytes()/1024:.1f} KB")
    st.metric("Bloom fill",   f"{bf.item_count / bf.expected_items * 100:.1f}%")
    st.divider()

    if st.button("Reset All State"):
        for k in ["page_store","bloom","reverse_index","cache","crawl_log","cache_log","riq","ri_worker"]:
            st.session_state.pop(k, None)
        _init_state()
        st.rerun()

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_search, tab_crawler, tab_bloom, tab_cache, tab_benchmarks = st.tabs([
    "Search", "Crawler", "BloomFilter", "Cache", "Benchmarks"
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — SEARCH
# ══════════════════════════════════════════════════════════════════════════════

with tab_search:
    st.header("Search")
    st.caption("Queries the reverse index built from crawled pages.")

    parser = QueryParser()
    col_q, col_btn = st.columns([5, 1])
    with col_q:
        raw_query = st.text_input(
            "Query", placeholder="e.g. python async  /  redis bloom filter",
            label_visibility="collapsed",
        )
    with col_btn:
        search_clicked = st.button("Search", type="primary", use_container_width=True)

    if raw_query and search_clicked:
        result = parser.parse(raw_query)
        if isinstance(result, ParseError):
            st.error(f"Parse error: {result.reason}")
            st.code(f'Input: "{result.raw}"')
        else:
            st.info(f"Terms: {result.terms}")
            with st.expander("Pipeline breakdown"):
                st.markdown(f"""
| Step | Output |
|------|--------|
| Raw | `{result.raw}` |
| Strip markup | `{result.raw}` |
| Tokenise | `{result.terms}` |
| Normalise | `{result.terms}` |
""")
            hits = ri.lookup_multi(result.terms)
            if not hits:
                st.warning("No results. Crawl pages first.")
            else:
                st.markdown(f"**{len(hits)} result(s)**")
                for rank, (page_id, score) in enumerate(hits[:10], 1):
                    page = ps.get_page(page_id)
                    if page:
                        with st.container(border=True):
                            st.markdown(f"**#{rank}  [{page.title}]({page.url})**  `score={score:.1f}`")
                            st.caption(page.url)
                            snippet = page.snippet[:300] + ("..." if len(page.snippet) > 300 else "")
                            st.write(snippet)
                            c1, c2, c3 = st.columns(3)
                            c1.caption(f"id: `{page.page_id}`")
                            c2.caption(f"children: {len(page.child_urls)}")
                            c3.caption(f"simhash: `{page.signature.value:016x}`")

    st.divider()
    st.subheader("URL Deduplication")
    raw_urls = st.text_area(
        "Paste URLs (one per line)",
        height=100,
        placeholder="https://example.com\nhttps://other.com\nhttps://example.com",
    )
    if raw_urls.strip():
        urls = [u.strip() for u in raw_urls.strip().splitlines() if u.strip()]
        dups = DedupJob.find_duplicates(urls)
        uniq = DedupJob.unique_urls(urls)
        c1, c2, c3 = st.columns(3)
        c1.metric("Total",      len(urls))
        c2.metric("Unique",     len(uniq))
        c3.metric("Duplicates", len(dups))
        if dups:
            for url, count in sorted(dups.items(), key=lambda x: -x[1]):
                st.text(f"{count}x  {url}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — CRAWLER
# ══════════════════════════════════════════════════════════════════════════════

with tab_crawler:
    st.header("Crawler")
    st.caption(
        "Fetches real pages over HTTP. "
        "robots.txt is respected. Rate limiting prevents hammering hosts. "
        "BloomFilter deduplicates URLs; Simhash deduplicates near-duplicate content."
    )

    col_left, col_right = st.columns([3, 1])
    with col_left:
        seed_input = st.text_area(
            "Seed URLs (one per line)",
            placeholder=(
                "https://python.org\n"
                "https://redis.io\n"
                "https://en.wikipedia.org/wiki/Bloom_filter"
            ),
            height=130,
        )
    with col_right:
        max_pages = st.number_input(
            "Max pages total", min_value=1, max_value=10_000, value=50,
            help="Hard budget across all domains.",
        )
        url_limit = st.number_input(
            "URL limit per domain", min_value=1, max_value=1_000, value=20,
            help="Cap on pages crawled from a single seed domain.",
        )
        rps = st.number_input(
            "Req/s per domain", min_value=0.1, max_value=5.0, value=1.0, step=0.1,
            format="%.1f",
            help="Politeness rate. 1 req/s is safe for most hosts.",
        )
        respect_robots = st.checkbox("Respect robots.txt", value=True)

    run_crawl = st.button("Run Crawl", type="primary")

    if run_crawl:
        seeds = [u.strip() for u in seed_input.strip().splitlines() if u.strip()]
        if not seeds:
            st.error("Enter at least one seed URL.")
        else:
            bad = [s for s in seeds
                   if urllib.parse.urlparse(s).scheme not in ("http", "https")]
            if bad:
                st.error(f"Invalid HTTP(S) URLs: {bad}")
            else:
                progress = st.progress(0, text="Starting crawl...")
                crawl_log: list[dict] = []

                crawler = Crawler(
                    seed_urls=seeds,
                    data_store=st.session_state.page_store,
                    bloom_filter=st.session_state.bloom,
                    max_pages=max_pages,
                    url_limit=url_limit,
                    requests_per_second=rps,
                    respect_robots=respect_robots,
                )

                crawled = 0
                while not crawler.frontier.empty() and crawled < max_pages:
                    try:
                        url = crawler.frontier.get(block=False)
                    except queue.Empty:
                        break

                    if crawler.bloom_filter.contains(url):
                        crawler.frontier.task_done()
                        crawl_log.append({"status": "bloom-skip", "url": url, "title": "", "children": ""})
                        continue

                    if not crawler._under_limit(url):
                        crawler.frontier.task_done()
                        crawl_log.append({"status": "limit-skip", "url": url, "title": "", "children": ""})
                        continue

                    if crawler._robots and not crawler._robots.allowed(url):
                        crawler.bloom_filter.add(url)
                        crawler.frontier.task_done()
                        crawl_log.append({"status": "robots-skip", "url": url, "title": "", "children": ""})
                        continue

                    if crawler._rate_limiter:
                        crawler._rate_limiter.wait(crawler._domain(url))

                    try:
                        title, content, children = _fetch(url)
                    except Exception as exc:
                        crawler.frontier.task_done()
                        crawl_log.append({"status": f"error: {type(exc).__name__}", "url": url, "title": "", "children": ""})
                        continue

                    page = Page.from_raw(url, title, content, children)

                    if crawler.data_store.crawled_similar(page.signature):
                        crawler.bloom_filter.add(url)
                        crawler.frontier.task_done()
                        crawl_log.append({"status": "near-dup", "url": url, "title": title, "children": ""})
                        continue

                    crawler.bloom_filter.add(url)
                    crawler.data_store.insert_crawled_link(page)
                    d = crawler._domain(url)
                    if d in crawler._domain_count:
                        crawler._domain_count[d] += 1
                    crawled += 1

                    for child in children:
                        crawler.frontier.put(child, priority=1)

                    st.session_state.riq.put({
                        "page_id": page.page_id, "url": page.url,
                        "title": page.title, "snippet": page.snippet,
                        "content": page.content,
                    })
                    crawler.frontier.task_done()
                    crawl_log.append({
                        "status": "crawled", "url": url,
                        "title": title[:80], "children": len(children),
                    })
                    progress.progress(
                        min(crawled / max_pages, 1.0),
                        text=f"Crawled {crawled}/{max_pages}  —  {url[:70]}",
                    )

                st.session_state.crawl_log.extend(crawl_log)
                time.sleep(0.4)
                progress.progress(
                    1.0,
                    text=f"Done — {crawled} pages crawled, {ri.word_count()} words indexed.",
                )
                st.rerun()

    if st.session_state.crawl_log:
        st.subheader("Crawl Log")
        log = st.session_state.crawl_log
        counts = Counter(e["status"] for e in log)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Crawled",      counts.get("crawled", 0))
        c2.metric("Bloom skip",   counts.get("bloom-skip", 0))
        c3.metric("Limit skip",   counts.get("limit-skip", 0))
        c4.metric("Near-dup",     counts.get("near-dup", 0))
        c5.metric("Robots skip",  counts.get("robots-skip", 0))
        st.dataframe(
            [{"Status": e["status"], "URL": e["url"],
              "Title": e.get("title",""), "Children": e.get("children","")}
             for e in log[-100:]],
            use_container_width=True, hide_index=True,
        )

    if ps.page_count() > 0:
        st.subheader(f"Crawled Pages ({ps.page_count()})")
        st.dataframe(
            [{"page_id": p.page_id, "URL": p.url, "Title": p.title[:70],
              "Snippet": p.snippet[:90], "SimHash": f"{p.signature.value:016x}",
              "Children": len(p.child_urls)}
             for p in ps._pages.values()],
            use_container_width=True, hide_index=True,
        )

        if ps.page_count() >= 2:
            st.subheader("Simhash Similarity Matrix")
            st.caption("Hamming distance (0 = identical, <=3 = near-duplicate)")
            import pandas as pd
            pages  = list(ps._pages.values())[:10]
            labels = [p.title[:25] for p in pages]
            matrix = [
                [p1.signature.hamming_distance(p2.signature) for p2 in pages]
                for p1 in pages
            ]
            df = pd.DataFrame(matrix, index=labels, columns=labels)
            st.dataframe(
                df.style.background_gradient(cmap="RdYlGn_r", vmin=0, vmax=32),
                use_container_width=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — BLOOM FILTER
# ══════════════════════════════════════════════════════════════════════════════

with tab_bloom:
    st.header("BloomFilter Inspector")
    bf = st.session_state.bloom

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("m (bits)",    f"{bf.bit_array_size:,}")
    c2.metric("k (hashes)",  bf.num_hash_functions)
    c3.metric("Items",       bf.item_count)
    c4.metric("Target FPR", f"{bf.target_false_positive_rate*100:.1f}%")
    c5.metric("Live FPR",   f"{bf.false_positive_rate()*100:.4f}%")

    c6, c7, c8 = st.columns(3)
    c6.metric("Memory (bytes)", f"{bf.memory_usage_bytes():,}")
    c7.metric("Memory (KB)",    f"{bf.memory_usage_bytes()/1024:.2f}")
    c8.metric("Fill ratio",     f"{bf.item_count / bf.expected_items * 100:.1f}%")

    fpr_live   = bf.false_positive_rate()
    fpr_target = bf.target_false_positive_rate
    st.progress(
        min(fpr_live / max(fpr_target, 1e-9), 1.0),
        text=f"Live FPR: {fpr_live*100:.4f}%  |  Target: {fpr_target*100:.1f}%",
    )

    st.divider()
    st.subheader("Membership Test")
    col_add, col_check = st.columns(2)

    with col_add:
        st.markdown("**Add item**")
        add_item = st.text_input("URL", key="bf_add", placeholder="https://example.com/page")
        if st.button("Add"):
            if add_item:
                positions = bf._positions(add_item)
                already   = bf.contains(add_item)
                bf.add(add_item)
                if already:
                    st.warning(f"Already present (or false positive): {add_item}")
                else:
                    st.success(f"Added.  Positions: {positions}")
                st.rerun()

    with col_check:
        st.markdown("**Check membership**")
        check_item = st.text_input("URL", key="bf_check", placeholder="https://example.com/page")
        if st.button("Check"):
            if check_item:
                positions   = bf._positions(check_item)
                found       = bf.contains(check_item)
                bits_at_pos = [int(bf._bits[p]) for p in positions]
                if found:
                    st.success(f"Probably present — all {bf.num_hash_functions} bits are 1")
                else:
                    st.error("Definitely NOT present — at least one bit is 0")
                st.code(f"Positions: {positions}\nBits:      {bits_at_pos}")

    st.divider()
    st.subheader("Bit Array (first 512 bits)")
    st.caption("Dark = 1, light = 0")
    sample_bits = [int(bf._bits[i]) for i in range(min(512, bf.bit_array_size))]
    rows        = [sample_bits[i:i+32] for i in range(0, len(sample_bits), 32)]
    html_rows   = []
    for row in rows:
        cells = "".join(
            f'<span style="display:inline-block;width:13px;height:13px;'
            f'background:{"#1E3A5F" if b else "#E5E7EB"};'
            f'border:1px solid #D1D5DB;margin:1px;"></span>'
            for b in row
        )
        html_rows.append(f"<div>{cells}</div>")
    st.html("".join(html_rows))
    set_count = sum(sample_bits)
    st.caption(f"{set_count}/{len(sample_bits)} bits set ({set_count/len(sample_bits)*100:.1f}%)")

    st.divider()
    st.subheader("Parameter Calculator")
    st.caption("Compute m (bit array size) and k (hash functions) for any (n, p) pair.")
    col_n, col_p = st.columns(2)
    with col_n:
        exp_n = st.slider("Expected items (n)", 1_000, 10_000_000, 1_000_000, step=1_000, format="%d")
    with col_p:
        exp_p = st.select_slider(
            "Target FPR (p)",
            options=[0.001, 0.005, 0.01, 0.02, 0.05, 0.1],
            value=0.01,
            format_func=lambda x: f"{x*100:.1f}%",
        )

    m_exp  = math.ceil(-exp_n * math.log(exp_p) / math.log(2)**2) + 1
    k_exp  = max(1, min(20, round((m_exp / exp_n) * math.log(2))))
    mem_kb = m_exp / 8 / 1024
    set_kb = exp_n * (8 + 57 + 60) / 1024

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("m (bits)",       f"{m_exp:,}")
    c2.metric("k",              k_exp)
    c3.metric("BloomFilter",    f"{mem_kb:.1f} KB")
    c4.metric("set() estimate", f"{set_kb:.1f} KB")
    c5.metric("Ratio",          f"{set_kb/mem_kb:.0f}x")

    st.code(
        f"m = ceil( -n*ln(p) / (ln2)^2 ) = ceil( -{exp_n:,}*ln({exp_p}) / {math.log(2)**2:.4f} ) = {m_exp:,}\n"
        f"k = round( (m/n)*ln2 )         = round( ({m_exp}/{exp_n})*{math.log(2):.4f} )         = {k_exp}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — CACHE
# ══════════════════════════════════════════════════════════════════════════════

with tab_cache:
    st.header("Akamai Cache Inspector")
    st.caption("BloomFilter admission gate: queries cached only after being seen at least twice.")

    cm       = st.session_state.cache
    bf_cache = cm.bloom_filter

    c1, c2, c3 = st.columns(3)
    c1.metric("Bloom m",      f"{bf_cache.bit_array_size:,}")
    c2.metric("Queries seen", bf_cache.item_count)
    c3.metric("FPR",          f"{bf_cache.false_positive_rate()*100:.3f}%")

    st.divider()
    st.subheader("Manual GET / SET")
    st.markdown(
        "**Protocol:** "
        "First GET: bloom miss — visit recorded, nothing returned. "
        "Second GET: bloom hit — cache lookup triggered. "
        "SET: stores the result for future GETs."
    )
    col_q2, col_v, col_b2 = st.columns([3, 2, 1])
    with col_q2:
        cache_query = st.text_input("Query key", placeholder="bloom filter", key="cache_q")
    with col_v:
        cache_value = st.text_input("JSON value", placeholder='{"results": []}', key="cache_v")
    with col_b2:
        cache_get = st.button("GET", use_container_width=True)
        cache_set = st.button("SET", use_container_width=True)

    if cache_query:
        if cache_get:
            bloom_seen = bf_cache.contains(cache_query)
            result     = cm.get(cache_query)
            outcome    = "HIT" if result is not None else ("BLOOM-MISS" if not bloom_seen else "CACHE-MISS")
            st.session_state.cache_log.append({
                "op": "GET", "query": cache_query,
                "bloom_seen": bloom_seen, "result": result, "outcome": outcome,
            })
            if result is not None:
                st.success(f"Cache HIT: {result}")
            elif not bloom_seen:
                st.info("BloomFilter miss — first visit recorded.")
            else:
                st.warning("BloomFilter hit — seen before, but not yet cached.")
            st.rerun()

        if cache_set and cache_value:
            try:
                val = json.loads(cache_value)
            except json.JSONDecodeError:
                val = cache_value
            cm.set(cache_query, val)
            st.success(f"Stored: {cache_query} -> {val}")
            st.session_state.cache_log.append({"op": "SET", "query": cache_query, "value": val})
            st.rerun()

    if st.session_state.cache_log:
        st.subheader("Operation Log")
        log_rows = []
        for e in st.session_state.cache_log[-30:]:
            if e["op"] == "GET":
                log_rows.append({
                    "Op": "GET", "Query": e["query"],
                    "Bloom": "seen" if e.get("bloom_seen") else "miss",
                    "Outcome": e.get("outcome",""),
                    "Result": str(e.get("result","null"))[:60],
                })
            else:
                log_rows.append({
                    "Op": "SET", "Query": e["query"],
                    "Bloom": "", "Outcome": "stored",
                    "Result": str(e.get("value",""))[:60],
                })
        st.dataframe(log_rows, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Traffic Simulation")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        sim_q = st.slider("Unique queries", 5, 100, 20)
    with col_s2:
        sim_r = st.slider("Repeats per query", 1, 20, 5)

    if st.button("Run Simulation"):
        sim_cm = CacheManager(expected_queries=10_000, false_positive_rate=0.01)
        bloom_misses, first_hits, cache_hits = 0, 0, 0
        queries = [f"query_{i}" for i in range(sim_q)]
        values  = {q: [f"r_{i}_{j}" for j in range(3)] for i, q in enumerate(queries)}
        for _ in range(sim_r):
            for q in queries:
                bloom_seen = sim_cm.bloom_filter.contains(q)
                result     = sim_cm.get(q)
                if not bloom_seen:
                    bloom_misses += 1
                elif result is None:
                    first_hits += 1
                    sim_cm.set(q, values[q])
                else:
                    cache_hits += 1
        total = sim_q * sim_r
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total requests",     total)
        c2.metric("Bloom misses (1st)", bloom_misses)
        c3.metric("Cache fills (2nd)",  first_hits)
        c4.metric("Cache hits (3rd+)",  cache_hits)
        st.info(
            f"{cache_hits/total*100:.0f}% of requests are cache hits after warm-up. "
            "One-shot queries never pollute the cache."
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — BENCHMARKS
# ══════════════════════════════════════════════════════════════════════════════

with tab_benchmarks:
    st.header("Benchmarks")
    st.caption(
        "Benchmarks run at N = 1k, 10k, 100k, 1M using real URL patterns. "
        "Re-generate: python benchmarks/plot_benchmarks.py"
    )

    plots_dir  = Path(__file__).parent / "benchmarks" / "plots"
    plot_files = [
        ("1_fpr_vs_fill.png",       "FPR vs Fill Level",
         "Empirical vs theoretical FPR as the filter fills. Theory tracks reality closely."),
        ("2_memory_comparison.png", "Memory: BloomFilter vs set()",
         "Log-log scale across N = 1k to 10M. BloomFilter stays ~80x smaller."),
        ("3_insert_speed.png",      "Insert Speed",
         "Time per insert (us) at each scale. k hash calls dominate BloomFilter cost."),
        ("4_lookup_speed.png",      "Lookup Speed — Hit and Miss",
         "HIT path: all k bits must be 1. MISS path: short-circuits on first 0-bit."),
        ("5_n_crossover.png",       "N-Crossover Analysis",
         "Memory ratio and absolute savings vs N."),
        ("6_scale_benchmark.png",   "Scale Benchmark: 1k / 10k / 100k / 1M",
         "Insert throughput (M/s), lookup latency (us), and memory at production-relevant scales."),
    ]

    for filename, title, caption in plot_files:
        path = plots_dir / filename
        st.subheader(title)
        st.caption(caption)
        if path.exists():
            st.image(str(path), use_container_width=True)
        else:
            st.warning(f"Not found: benchmarks/plots/{filename}  —  run  python benchmarks/plot_benchmarks.py")
        st.divider()

    import pandas as pd
    st.subheader("N-Crossover Table (p = 1%)")
    st.caption("BloomFilter vs Python set() for ~60-byte URL strings.")
    ns   = [1_000, 10_000, 100_000, 1_000_000, 10_000_000]
    rows = []
    for n in ns:
        m    = math.ceil(-n * math.log(0.01) / math.log(2)**2) + 1
        k    = max(1, min(20, round((m / n) * math.log(2))))
        b_kb = m / 8 / 1024
        s_kb = n * (8 + 57 + 60) / 1024
        rows.append({
            "N":                f"{n:,}",
            "m (bits)":         f"{m:,}",
            "k":                k,
            "BloomFilter (KB)": f"{b_kb:.1f}",
            "set() (KB)":       f"{s_kb:.1f}",
            "Ratio":            f"{s_kb/b_kb:.0f}x",
            "Saved (MB)":       f"{(s_kb-b_kb)/1024:.2f}",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.info(
        "BloomFilter is ~80x more memory-efficient than a Python set() at all values of N. "
        "Absolute savings reach 90 MB at N = 1,000,000."
    )
