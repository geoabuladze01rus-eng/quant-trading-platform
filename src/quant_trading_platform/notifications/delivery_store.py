"""Records which signal ids were already delivered, so restarts do not resend them.

The file store writes atomically (temp file, then os.replace). A corrupt file
raises instead of being treated as empty, because treating it as empty would
resend every signal.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol


class DeliveryStoreError(RuntimeError):
    pass


class DeliveredSignalStore(Protocol):
    def contains(self, signal_id: str) -> bool: ...

    def add(self, signal_id: str) -> None: ...


class InMemoryDeliveryStore:
    def __init__(self) -> None:
        self._ids: set[str] = set()

    def contains(self, signal_id: str) -> bool:
        return signal_id in self._ids

    def add(self, signal_id: str) -> None:
        self._ids.add(signal_id)


class FileDeliveryStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._ids = self._load()

    def _load(self) -> set[str]:
        if not self._path.exists():
            return set()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise DeliveryStoreError("delivery store is unreadable; refusing to resend") from None
        if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
            raise DeliveryStoreError("delivery store has an unexpected format")
        return set(data)

    def contains(self, signal_id: str) -> bool:
        return signal_id in self._ids

    def add(self, signal_id: str) -> None:
        if signal_id in self._ids:
            return
        self._ids.add(signal_id)
        try:
            self._write()
        except OSError:
            self._ids.discard(signal_id)
            raise DeliveryStoreError("could not persist delivery record") from None

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, prefix=".delivered-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(sorted(self._ids), handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self._path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
