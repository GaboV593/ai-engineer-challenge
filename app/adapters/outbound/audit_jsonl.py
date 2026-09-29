"""Auditoría append-only en JSONL. Implementa AuditPort."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class JsonlAuditLogger:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(self, trace_id: str, event: str, **fields: Any) -> None:
        record = {"ts": datetime.now(UTC).isoformat(), "trace_id": trace_id, "event": event, **fields}
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock, self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
