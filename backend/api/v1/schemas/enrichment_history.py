from infrastructure.msgspec_fastapi import AppStruct
from models.enrichment_history import EnrichmentHistoryEntry


class EnrichmentHistoryItem(AppStruct):
    """One history row as the UI needs it.

    Deliberately not the stored entry: that carries `snapshot_json`, a complete
    tag snapshot of the file before the write. A hundred of those is megabytes
    of payload the browser has no use for - only restore reads it, server-side.
    """

    id: str
    file_path: str
    kinds: tuple[str, ...]
    changed_fields: tuple[str, ...]
    created_at: float
    restored_at: float | None = None

    @classmethod
    def from_entry(cls, entry: EnrichmentHistoryEntry) -> "EnrichmentHistoryItem":
        return cls(
            id=entry.id,
            file_path=entry.file_path,
            kinds=entry.kinds,
            changed_fields=entry.changed_fields,
            created_at=entry.created_at,
            restored_at=entry.restored_at,
        )


class EnrichmentHistoryPage(AppStruct):
    items: tuple[EnrichmentHistoryItem, ...] = ()
