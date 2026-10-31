"""Capability boundary for future, locally verified Douyin adapters."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SourceCapabilities:
    keyword_search: bool = False
    video_details: bool = False
    author_episodes: bool = False
    collection_episodes: bool = False
    manual_import: bool = True


class SourceAdapter(Protocol):
    capabilities: SourceCapabilities

    def search(self, keyword: str, sort_order: str) -> list[dict]: ...
    def video_details(self, video_id: str) -> dict: ...
    def author_episodes(self, account_id: str) -> list[dict]: ...
    def collection_episodes(self, collection_id: str) -> list[dict]: ...


MANUAL_CAPABILITIES = SourceCapabilities()
