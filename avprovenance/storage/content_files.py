"""Content-addressed media files with path checks and atomic writes."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..errors import PersistenceFailure
from ..serialization import sha256_hex


_DIGEST_PREFIXES = tuple(f"{value:02x}" for value in range(256))


class ContentFileRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key_for_digest(digest: str) -> str:
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise PersistenceFailure("A content digest has an invalid shape.")
        return f"{digest[:2]}/{digest[2:4]}/{digest}"

    def _path_for_key(self, key: str) -> Path:
        parts = Path(key).parts
        if len(parts) != 3:
            raise PersistenceFailure("A content key has an invalid shape.")
        root = self.root.resolve()
        candidate = (self.root / key).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PersistenceFailure("A content key escapes the data root.") from exc
        return candidate

    def write(self, data: bytes, digest: str) -> str:
        if sha256_hex(data) != digest:
            raise PersistenceFailure("Content digest does not match the supplied bytes.")
        key = self.key_for_digest(digest)
        target = self._path_for_key(key)
        if target.exists():
            return key
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".content-",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        )
        temporary_path = Path(temporary.name)
        try:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary.close()
            os.replace(temporary_path, target)
        except OSError as exc:
            temporary.close()
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise PersistenceFailure("Media bytes could not be saved.") from exc
        return key

    def read(self, key: str) -> bytes:
        path = self._path_for_key(key)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise PersistenceFailure("A referenced media file is missing.") from exc

    def remove(self, key: str) -> None:
        path = self._path_for_key(key)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return

    def exists(self, key: str) -> bool:
        return self._path_for_key(key).is_file()

    def is_valid_key(self, key: Any) -> bool:
        if not isinstance(key, str):
            return False
        parts = Path(key).parts
        if len(parts) != 3:
            return False
        first, second, digest = parts
        return (
            len(first) == 2
            and first in _DIGEST_PREFIXES
            and len(second) == 2
            and second in _DIGEST_PREFIXES
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
        )

    def garbage_collect(self, referenced_keys: Iterable[str]) -> dict[str, int]:
        """Remove interrupted and unreferenced bytes while preserving references.

        Only two kinds of files are deleted:

        * temporary siblings left by a process killed mid-write;
        * content-addressed blobs whose key is absent from
          ``referenced_keys`` (the content keys of every surviving asset).

        Blobs referenced by any valid asset are never touched.  Empty
        shard directories created and abandoned by interrupted writes are
        pruned as well.  The operation is idempotent.
        """
        root = self.root.resolve()
        retained = {key for key in referenced_keys if self.is_valid_key(key)}
        removed_blobs = 0
        removed_temporaries = 0

        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name
            if name.startswith(".content-") and name.endswith(".tmp"):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
                removed_temporaries += 1
                continue
            try:
                relative = path.resolve().relative_to(root)
            except (OSError, ValueError):
                continue
            key = relative.as_posix()
            if not self.is_valid_key(key):
                # Unknown layout (including staged temp files with other
                # names) is left untouched rather than guessed at.
                continue
            if key not in retained:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
                removed_blobs += 1

        for directory in sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            reverse=True,
        ):
            try:
                next(directory.iterdir())
            except StopIteration:
                try:
                    directory.rmdir()
                except OSError:
                    pass
            except OSError:
                continue

        return {
            "removed_blobs": removed_blobs,
            "removed_temporaries": removed_temporaries,
        }
