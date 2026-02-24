import queue, threading
import pytest
from bloomcrawl.crawler.url_frontier import URLFrontier


class TestBasic:
    def test_put_get(self):
        f = URLFrontier(); f.put("https://a.com")
        assert f.get(block=False) == "https://a.com"

    def test_priority_order(self):
        f = URLFrontier()
        f.put("https://low.com", 5); f.put("https://high.com", 1); f.put("https://mid.com", 3)
        assert f.get(block=False) == "https://high.com"

    def test_empty(self):          assert URLFrontier().empty()
    def test_not_empty_after_put(self):
        f = URLFrontier(); f.put("x"); assert not f.empty()
    def test_size(self):
        f = URLFrontier(); f.put("a"); f.put("b"); assert f.size() == 2
    def test_raises_on_empty(self):
        with pytest.raises(queue.Empty): URLFrontier().get(block=False)


class TestThreadSafety:
    def test_concurrent_puts_and_gets(self):
        f, results, errors = URLFrontier(), [], []
        def producer(start, n):
            for i in range(start, start + n): f.put(f"https://site-{i}.com", i % 10)
        def consumer(n):
            for _ in range(n):
                try: results.append(f.get(block=True, timeout=2)); f.task_done()
                except queue.Empty: errors.append(1)
        threads = [threading.Thread(target=producer, args=(0, 100)),
                   threading.Thread(target=producer, args=(100, 100)),
                   threading.Thread(target=consumer, args=(200,))]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)
        assert not errors and len(results) == 200 and len(set(results)) == 200
