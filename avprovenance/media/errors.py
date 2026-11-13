"""Media-layer failures that are independent of HTTP concerns."""

from __future__ import annotations


class MediaOperationFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
