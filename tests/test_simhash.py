import pytest
from bloomcrawl.core.simhash import Simhash


class TestConstruction:
    def test_returns_instance(self):         assert isinstance(Simhash.from_text("hello"), Simhash)
    def test_deterministic(self):            assert Simhash.from_text("x").value == Simhash.from_text("x").value
    def test_different_text_differs(self):   assert Simhash.from_text("alpha beta").value != Simhash.from_text("gamma delta")
    def test_empty_string(self):             assert isinstance(Simhash.from_text(""), Simhash)
    def test_fits_64_bits(self):             assert 0 <= Simhash.from_text("a" * 1000).value < (1 << 64)


class TestHamming:
    def test_identical_zero(self):           assert Simhash.from_text("x").hamming_distance(Simhash.from_text("x")) == 0
    def test_symmetric(self):
        a, b = Simhash.from_text("foo"), Simhash.from_text("bar")
        assert a.hamming_distance(b) == b.hamming_distance(a)
    def test_non_negative(self):             assert Simhash.from_text("a").hamming_distance(Simhash.from_text("b")) >= 0
    def test_max_64(self):                   assert Simhash.from_text("abc").hamming_distance(Simhash.from_text("xyz")) <= 64
    def test_similar_text_bounded(self):
        a = Simhash.from_text("the quick brown fox jumps over the lazy dog")
        b = Simhash.from_text("the quick brown fox leaps over the lazy dog")
        assert a.hamming_distance(b) <= 25
    def test_very_different_high(self):
        a = Simhash.from_text("machine learning neural networks")
        b = Simhash.from_text("underwater basket weaving coral reef")
        assert a.hamming_distance(b) > 5


class TestSimilar:
    def test_same_is_similar(self):          assert Simhash.from_text("abc").is_similar(Simhash.from_text("abc"), 0)
    def test_near_dup(self):
        a = Simhash.from_text("buy cheap flights online book today")
        b = Simhash.from_text("buy cheap flights online book today discount")
        assert a.is_similar(b, threshold=15)
    def test_unrelated_not_similar(self):
        assert not Simhash.from_text("python interpreter").is_similar(
            Simhash.from_text("sushi restaurant tokyo"), threshold=3)
