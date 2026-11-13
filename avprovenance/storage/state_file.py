"""Atomic JSON state file with serialized in-process mutations.

Durability model
----------------

Every mutation writes a pre-image journal (``.state.json.recovery``) before
the committed ``state.json`` is replaced.  The journal is *only* consulted
when no readable committed state exists:

* A healthy ``state.json`` is always treated as the most recent durable
  fact.  A journal left behind because the process was killed after the
  atomic replacement but before journal removal is stale and discarded.
* When ``state.json`` is missing or unreadable, a structurally valid
  journal replaces it; otherwise the corrupt file is quarantined and the
  service starts from an empty default state instead of failing to boot.

Because ``os.replace`` is atomic, the committed state never contains a
partially written document, and recovery is idempotent across restarts.
"""

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
        self._staging_path = self.recovery_path.with_suffix(".tmp")
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._staging_path.unlink(missing_ok=True)
            self._remove_stale_siblings_unlocked()
            self._recover_unlocked()

    def _remove_stale_siblings_unlocked(self) -> None:
        """Discard temp files left by a process killed mid write."""
        try:
            siblings = self.path.parent.iterdir()
        except OSError:
            return
        for sibling in siblings:
            if not sibling.is_file():
                continue
            name = sibling.name
            if (
                name.startswith(".state-")
                and name.endswith(".tmp")
            ) or name == self._staging_path.name:
                sibling.unlink(missing_ok=True)

    @staticmethod
    def _is_valid_state(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        return all(key in value for key in DEFAULT_STATE)

    def _fsync_directory_unlocked(self) -> None:
        try:
            descriptor = os.open(self.path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _quarantine_unlocked(self, path: Path) -> None:
        if not path.exists():
            return
        counter = 0
        while True:
            suffix = f".corrupt-{counter}" if counter else ".corrupt"
            destination = path.with_name(path.name + suffix)
            if not destination.exists():
                try:
                    os.replace(path, destination)
                except OSError:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                return
            counter += 1

    def _read_json_file_unlocked(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _recover_unlocked(self) -> None:
        """Establish one readable committed state without downgrading it."""
        committed: Any = None
        if self.path.is_file():
            try:
                committed = self._read_json_file_unlocked(self.path)
            except (OSError, json.JSONDecodeError):
                committed = None
            if not self._is_valid_state(committed):
                committed = None

        if committed is not None:
            # The committed state is the newest durable fact.  Any journal
            # still present predates it and must not roll it back.
            self.recovery_path.unlink(missing_ok=True)
            return

        journal: Any = None
        if self.recovery_path.is_file():
            try:
                journal = self._read_json_file_unlocked(self.recovery_path)
            except (OSError, json.JSONDecodeError):
                journal = None

        if self._is_valid_state(journal):
            self._write_unlocked(journal)
            self.recovery_path.unlink(missing_ok=True)
            self._fsync_directory_unlocked()
            return

        # Neither document can serve as state.  Quarantine the damage and
        # keep the service available with an empty catalog.
        if self.path.is_file():
            self._quarantine_unlocked(self.path)
        if self.recovery_path.is_file():
            self._quarantine_unlocked(self.recovery_path)
        self._write_unlocked(deepcopy(DEFAULT_STATE))
        self._fsync_directory_unlocked()

    def _stage_interrupted_write_unlocked(self, value: dict[str, Any]) -> None:
        try:
            with self._staging_path.open("w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(self._staging_path, self.recovery_path)
            self._fsync_directory_unlocked()
        except OSError as exc:
            self._staging_path.unlink(missing_ok=True)
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
            self._fsync_directory_unlocked()
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
            self._fsync_directory_unlocked()
            return result
