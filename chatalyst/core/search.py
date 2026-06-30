from __future__ import annotations

from rapidfuzz import fuzz

from chatalyst.core.cache import ChatCache
from chatalyst.core.models import SearchResult


class SearchEngine:
    """Search facade combining SQLite FTS with lightweight fuzzy re-ranking."""

    def __init__(self, cache: ChatCache) -> None:
        self.cache = cache

    def search(self, query: str, *, limit: int = 50) -> list[SearchResult]:
        results = self.cache.search_fts(query, limit=limit)
        if not results:
            return []
        query_text = query.lower()
        reranked = [
            result.model_copy(
                update={
                    "score": result.score
                    + (fuzz.partial_ratio(query_text, result.title.lower()) / 100.0)
                    + (fuzz.partial_ratio(query_text, result.snippet.lower()) / 200.0)
                }
            )
            for result in results
        ]
        return sorted(reranked, key=lambda item: item.score, reverse=True)[:limit]
