import math
import mmh3
from bitarray import bitarray


class BloomFilter:
    """
    Probabilistic set. No false negatives; FPR ≈ false_positive_rate at capacity.

    Backing store : bitarray (C ext) — 8× memory savings vs bytearray.
    Hash strategy : MurmurHash3 double-hashing — h_i = (h1 + i*h2) % m.
                    h2 forced odd to prevent cyclic clustering.
                    2 hash calls + k cheap linear combos (Kirsch & Mitzenmacher 2006).
    Parameters    : m and k derived from (n, p) — identical to Redis/Cassandra/Guava.
    """

    def __init__(self, expected_items: int, false_positive_rate: float) -> None:
        if expected_items < 1:
            raise ValueError(f"expected_items must be >= 1, got {expected_items}")
        if not (0 < false_positive_rate < 1):
            raise ValueError(f"false_positive_rate must be in (0,1), got {false_positive_rate}")

        self._n = expected_items
        self._p = false_positive_rate
        # m = ceil(-n·ln(p) / (ln2)²) + 1 safety margin
        self._m = math.ceil(-expected_items * math.log(false_positive_rate) / math.log(2) ** 2) + 1
        # k = round((m/n)·ln2), clamped to [1, 20]
        self._k = max(1, min(20, round((self._m / expected_items) * math.log(2))))
        self._bits = bitarray(self._m)
        self._bits.setall(0)
        self._count = 0

    def add(self, item: str) -> None:
        for pos in self._positions(item):
            self._bits[pos] = 1
        self._count += 1

    def contains(self, item: str) -> bool:
        return all(self._bits[pos] for pos in self._positions(item))

    def false_positive_rate(self) -> float:
        """Theoretical FPR at current fill: (1 − e^(−k·count/m))^k."""
        return (1.0 - math.exp(-self._k * self._count / self._m)) ** self._k

    def memory_usage_bytes(self) -> int:
        return self._bits.buffer_info()[1]

    @property
    def bit_array_size(self) -> int:               return self._m
    @property
    def num_hash_functions(self) -> int:           return self._k
    @property
    def item_count(self) -> int:                   return self._count
    @property
    def expected_items(self) -> int:               return self._n
    @property
    def target_false_positive_rate(self) -> float: return self._p

    def _positions(self, item: str) -> list[int]:
        b = item.encode()
        h1 = mmh3.hash(b, seed=0, signed=False)
        h2 = mmh3.hash(b, seed=1, signed=False) | 1  # odd step → no clustering
        return [(h1 + i * h2) % self._m for i in range(self._k)]
