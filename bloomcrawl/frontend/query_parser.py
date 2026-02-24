from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Union

_STOP = frozenset({
    "a","an","the","and","or","but","in","on","at","to","for","of","with","by","from","is","was",
})
_MAX_LEN = 500


@dataclass(frozen=True)
class ParsedQuery:
    terms: list[str]
    raw: str


@dataclass(frozen=True)
class ParseError:
    reason: str
    raw: str


Result = Union[ParsedQuery, ParseError]


class QueryParser:
    """
    4-step pipeline returning ParsedQuery or ParseError (typed, never silenced).
    Steps: validate → strip_markup → tokenise → normalise.
    Callers map ParseError to HTTP 400.
    """

    def parse(self, raw: str) -> Result:
        r = self._validate(raw)
        if isinstance(r, ParseError): return r
        r = self._strip_markup(r)
        if isinstance(r, ParseError): return r
        r = self._tokenise(r)
        if isinstance(r, ParseError): return r
        return self._normalise(r)

    def _validate(self, raw: str) -> Result:
        if not raw or not raw.strip():
            return ParseError("query is empty", raw)
        if len(raw) > _MAX_LEN:
            return ParseError(f"query exceeds {_MAX_LEN} characters", raw)
        return ParsedQuery([raw], raw)

    def _strip_markup(self, q: ParsedQuery) -> Result:
        cleaned = re.sub(r"<[^>]+>", " ", q.terms[0]).strip()
        return ParsedQuery([cleaned], q.raw) if cleaned else ParseError("empty after markup removal", q.raw)

    def _tokenise(self, q: ParsedQuery) -> Result:
        tokens = re.findall(r"[a-zA-Z0-9]+", q.terms[0].lower())
        return ParsedQuery(tokens, q.raw) if tokens else ParseError("no alphanumeric tokens found", q.raw)

    def _normalise(self, q: ParsedQuery) -> Result:
        seen: set[str] = set()
        terms = []
        for t in q.terms:
            if t not in _STOP and t not in seen and len(t) > 1:
                seen.add(t)
                terms.append(t)
        return ParsedQuery(terms, q.raw) if terms else ParseError("all tokens were stop words", q.raw)
