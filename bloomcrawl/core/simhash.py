import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Simhash:
    """
    64-bit content fingerprint for near-duplicate detection.
    Algorithm: weight-vote over word-shingle MD5 hashes → 64-bit fingerprint.
    Two pages are similar when hamming_distance(a, b) <= threshold.
    """
    value: int

    @classmethod
    def from_text(cls, text: str, shingle_size: int = 3) -> "Simhash":
        tokens = text.lower().split()
        shingles = (
            [" ".join(tokens[i:i + shingle_size])
             for i in range(max(1, len(tokens) - shingle_size + 1))]
            or [text or " "]
        )
        weights = [0] * 64
        for s in shingles:
            h = int(hashlib.md5(s.encode()).hexdigest(), 16) & ((1 << 64) - 1)
            for bit in range(64):
                weights[bit] += 1 if (h >> bit) & 1 else -1
        return cls(value=sum((1 << b) for b in range(64) if weights[b] > 0))

    def hamming_distance(self, other: "Simhash") -> int:
        return bin(self.value ^ other.value).count("1")

    def is_similar(self, other: "Simhash", threshold: int = 3) -> bool:
        return self.hamming_distance(other) <= threshold
