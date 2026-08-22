from typing import Protocol

from pydantic import BaseModel, Field


class CandidateItem(BaseModel):
    source: str
    title: str
    url: str
    tagline: str = ""
    metrics: dict = Field(default_factory=dict)
    topics: list[str] = Field(default_factory=list)
    rank: int | None = None


class SourceError(RuntimeError):
    pass


class SourceAdapter(Protocol):
    name: str

    def fetch(self) -> list[CandidateItem]: ...


def dedupe(items: list[CandidateItem]) -> list[CandidateItem]:
    seen: set[str] = set()
    out: list[CandidateItem] = []
    for item in items:
        key = item.url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
