from typing import Any

from schemas.document import Document

NOTE = "This may not be the original source."


def build_source_history(documents: list[Document]) -> dict[str, Any]:
    dated = sorted(
        (d for d in documents if d.published_at is not None),
        key=lambda d: d.published_at.isoformat(),
    )
    undated = [d for d in documents if d.published_at is None]

    def row(document: Document) -> dict[str, Any]:
        return {
            "title": document.title,
            "publisher": document.domain,
            "date": document.published_raw,
            "url": document.canonical_url,
        }

    return {
        "earliest_found": row(dated[0]) if dated else None,
        "earliest_label": "Earliest source found in this audit",
        "note": NOTE,
        "dated": [row(d) for d in dated],
        "undated": [row(d) for d in undated],
    }
