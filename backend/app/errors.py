from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Stable API-domain error envelope shared by database-backed services."""

    def __init__(
        self,
        code: str,
        message_key: str,
        *,
        status_code: int = 400,
        field_path: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.message_key = message_key
        self.status_code = status_code
        self.field_path = field_path
        self.retryable = retryable
        self.details = details or {}

    def response(self) -> dict[str, Any]:
        return {
            "accepted": False,
            "error_code": self.code,
            "field_path": self.field_path,
            "retryable": self.retryable,
            "message_key": self.message_key,
            "details": self.details,
        }
