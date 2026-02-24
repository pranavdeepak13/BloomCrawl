from __future__ import annotations
import queue, re, threading
from bloomcrawl.workers.reverse_index import ReverseIndex

_STOP = frozenset({
    "a","an","the","and","or","but","in","on","at","to","for",
    "of","with","by","from","is","was","are","were","be","been",
    "it","its","this","that","as","if","not","no",
})


def _tokenise(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+", text.lower())
            if t not in _STOP and len(t) > 1]


class ReverseIndexWorker:
    """Daemon thread consuming reverse_index_queue. Title words scored 2×, body 1×."""

    def __init__(self, q: queue.Queue, index: ReverseIndex) -> None:
        self._q, self._idx = q, index
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None: self._t.start()

    def stop(self) -> None:
        self._q.put(None)  # poison pill
        self._t.join(timeout=5)

    def _run(self) -> None:
        while True:
            try:
                msg = self._q.get(timeout=1)
            except queue.Empty:
                continue
            if msg is None:
                break
            pid = msg["page_id"]
            title_words = set(_tokenise(msg.get("title", "")))
            for word in set(_tokenise(msg.get("title", "") + " " + msg.get("content", ""))):
                self._idx.add(word, pid, score=2.0 if word in title_words else 1.0)
            self._q.task_done()
