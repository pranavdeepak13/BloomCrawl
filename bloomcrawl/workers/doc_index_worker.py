from __future__ import annotations
import queue, threading
from bloomcrawl.crawler.page_store import PageStore


class DocIndexWorker:
    """Daemon thread consuming doc_index_queue. Hook for a future document DB write."""

    def __init__(self, q: queue.Queue, store: PageStore) -> None:
        self._q, self._store = q, store
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None: self._t.start()

    def stop(self) -> None:
        self._q.put(None)
        self._t.join(timeout=5)

    def _run(self) -> None:
        while True:
            try:
                msg = self._q.get(timeout=1)
            except queue.Empty:
                continue
            if msg is None:
                break
            # PageStore already holds title/snippet; extend here for a separate doc DB.
            self._q.task_done()
