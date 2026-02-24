import hashlib
from dataclasses import dataclass, field
from bloomcrawl.core.simhash import Simhash


@dataclass
class Page:
    """Single crawled page. Signature auto-computed from content on init."""
    page_id: str
    url: str
    title: str
    snippet: str
    content: str
    child_urls: list[str] = field(default_factory=list)
    signature: Simhash = field(init=False)

    def __post_init__(self) -> None:
        self.signature = Simhash.from_text(self.content)

    @classmethod
    def from_raw(cls, url: str, title: str, content: str, child_urls: list[str]) -> "Page":
        return cls(
            page_id=hashlib.sha256(url.encode()).hexdigest()[:16],
            url=url,
            title=title,
            snippet=content[:200].strip(),
            content=content,
            child_urls=child_urls,
        )
