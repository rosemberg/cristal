"""Domain entity: Page."""

from __future__ import annotations

from dataclasses import dataclass, field

_VALID_CONTENT_TYPES = frozenset({"page", "pdf", "csv", "video", "api"})


@dataclass
class Page:
    id: int
    url: str
    title: str
    content_type: str  # page | pdf | csv | video | api
    depth: int
    description: str | None = None
    main_content: str | None = None
    content_summary: str | None = None
    category: str | None = None
    subcategory: str | None = None
    parent_url: str | None = None
    breadcrumb: list[dict[str, object]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)  # type: ignore[name-defined]  # noqa: F821

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("Page URL cannot be empty")
        if self.content_type not in _VALID_CONTENT_TYPES:
            raise ValueError(
                f"Invalid content_type: {self.content_type!r}. "
                f"Expected one of {sorted(_VALID_CONTENT_TYPES)}"
            )
