import queue
from dataclasses import dataclass, field


@dataclass(order=True)
class _Entry:
    priority: int
    url: str = field(compare=False)


class URLFrontier:
    """
    Thread-safe priority queue of URLs to crawl.
    Uses queue.PriorityQueue (stdlib heapq + mutex) — no external locking needed.
    Convention: lower priority integer = crawl sooner (0 = seed).
    """

    def __init__(self) -> None:
        self._q: queue.PriorityQueue[_Entry] = queue.PriorityQueue()

    def put(self, url: str, priority: int = 0) -> None:
        self._q.put(_Entry(priority, url))

    def get(self, block: bool = True, timeout: float | None = None) -> str:
        return self._q.get(block=block, timeout=timeout).url

    def task_done(self) -> None: self._q.task_done()
    def empty(self) -> bool:     return self._q.empty()
    def size(self) -> int:       return self._q.qsize()
