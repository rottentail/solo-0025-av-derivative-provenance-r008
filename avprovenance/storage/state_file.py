"""Atomic JSON state file with serialized in-process mutations."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, TypeVar

from ..errors import PersistenceFailure


T = TypeVar("T")

DEFAULT_STATE: dict[str, Any] = {
    "schema_version": 1,
    "assets": {},
    "renditions": {},
    "provenance_edges": [],
    "integrity_checks": {},
}


class JsonStateFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.recovery_path = path.with_name(f".{path.name}.recovery")
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not self.path.exists():
                self._write_unlocked(deepcopy(DEFAULT_STATE))
            self._restore_interrupted_write_unlocked()

    def _restore_interrupted_write_unlocked(self) -> None:
        if not self.recovery_path.is_file():
            return
        try:
            candidate = json.loads(
                self.recovery_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            self.recovery_path.unlink(missing_ok=True)
            return
        if not isinstance(candidate, dict):
            self.recovery_path.unlink(missing_ok=True)
            return
        self._write_unlocked(candidate)
        self.recovery_path.unlink(missing_ok=True)

    def _stage_interrupted_write_unlocked(self, value: dict[str, Any]) -> None:
        temporary = self.recovery_path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.recovery_path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise PersistenceFailure(
                "The state recovery record could not be saved."
            ) from exc

    def _load_unlocked(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceFailure("The durable state file is unreadable.") from exc
        if not isinstance(value, dict):
            raise PersistenceFailure("The durable state root must be an object.")
        for key, default in DEFAULT_STATE.items():
            value.setdefault(key, deepcopy(default))
        return value

    def _write_unlocked(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".state-",
            suffix=".tmp",
            dir=self.path.parent,
            delete=False,
        )
        temporary_path = Path(temporary.name)
        try:
            json.dump(
                value,
                temporary,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary.close()
            os.replace(temporary_path, self.path)
        except OSError as exc:
            temporary.close()
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise PersistenceFailure("The durable state file could not be written.") from exc

    def read(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._load_unlocked())

    def mutate(self, mutation: Callable[[dict[str, Any]], T]) -> T:
        with self._lock:
            state = self._load_unlocked()
            self._stage_interrupted_write_unlocked(deepcopy(state))
            result = mutation(state)
            self._write_unlocked(state)
            self.recovery_path.unlink(missing_ok=True)
            return result
