import pytest
from bloomcrawl.frontend.query_parser import QueryParser, ParsedQuery, ParseError


class TestHappyPath:
    P = QueryParser()

    def test_simple(self):
        r = self.P.parse("machine learning")
        assert isinstance(r, ParsedQuery) and "machine" in r.terms

    def test_normalised_lowercase(self):
        r = self.P.parse("Machine Learning")
        assert isinstance(r, ParsedQuery) and all(t == t.lower() for t in r.terms)

    def test_stop_words_removed(self):
        r = self.P.parse("the best way to learn")
        assert isinstance(r, ParsedQuery) and "the" not in r.terms and "to" not in r.terms

    def test_deduplication(self):
        r = self.P.parse("python python python")
        assert isinstance(r, ParsedQuery) and r.terms.count("python") == 1

    def test_raw_preserved(self):
        r = self.P.parse("Deep Learning")
        assert isinstance(r, ParsedQuery) and r.raw == "Deep Learning"

    def test_markup_stripped(self):
        r = self.P.parse("<b>python</b> programming")
        assert isinstance(r, ParsedQuery) and "python" in r.terms

    def test_xss_stripped(self):
        r = self.P.parse("<script>alert(1)</script>python")
        assert isinstance(r, ParsedQuery) and "python" in r.terms


class TestErrors:
    P = QueryParser()

    def test_empty(self):              assert isinstance(self.P.parse(""), ParseError)
    def test_whitespace(self):         assert isinstance(self.P.parse("   "), ParseError)
    def test_too_long(self):           assert isinstance(self.P.parse("a" * 501), ParseError)
    def test_pure_markup(self):        assert isinstance(self.P.parse("<div><span></span></div>"), ParseError)
    def test_all_stop_words(self):     assert isinstance(self.P.parse("the and or but in on at"), ParseError)
    def test_single_char_filtered(self):
        r = self.P.parse("a b c python")
        assert isinstance(r, ParsedQuery) and "a" not in r.terms and "python" in r.terms

    @pytest.mark.parametrize("query,err", [
        ("", True), ("   ", True), ("a", True),
        ("python", False), ("<b>code</b>", False), ("the or and", True),
        ("'; DROP TABLE pages; --", False),  # SQL injection — parses to tokens, no side effect
        ("café résumé", False),              # unicode — parsed to latin tokens
    ])
    def test_parametrised(self, query, err):
        r = self.P.parse(query)
        assert isinstance(r, ParseError) == err, f"query={query!r}: expected err={err}, got {r}"
