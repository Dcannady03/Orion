"""Structured results shared by Orion's CLI and future interfaces."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from types import MappingProxyType
from typing import Any, Mapping


_STATUSES = frozenset({"success", "warning", "failure"})
_JSON_SCALARS = (str, int, float, bool, type(None))


def _freeze_json(value: Any) -> Any:
    """Copy a JSON-compatible value into an immutable representation."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("Application result object keys must be strings.")
        return MappingProxyType({
            key: _freeze_json(item)
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError("Application result numbers must be finite.")
    if isinstance(value, _JSON_SCALARS):
        return value
    raise TypeError(
        "Application result data must contain only JSON-compatible values; "
        f"received {type(value).__name__}."
    )


def _thaw_json(value: Any) -> Any:
    """Return ordinary dictionaries and lists suitable for JSON encoders."""
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _strings(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if isinstance(values, str):
        return (values,)
    return tuple(str(value) for value in values)


@dataclass(frozen=True)
class ApplicationResult:
    """An immutable, serializable outcome from Orion's application layer."""

    status: str
    message: str
    data: Mapping[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized_status = str(self.status).strip().lower()
        if normalized_status not in _STATUSES:
            raise ValueError(
                "Application result status must be success, warning, or failure."
            )
        object.__setattr__(self, "status", normalized_status)
        object.__setattr__(self, "message", str(self.message))
        object.__setattr__(self, "data", _freeze_json(dict(self.data)))
        object.__setattr__(self, "warnings", _strings(self.warnings))
        object.__setattr__(self, "errors", _strings(self.errors))
        object.__setattr__(self, "next_actions", _strings(self.next_actions))

    @classmethod
    def success(
        cls,
        message: str,
        *,
        data: Mapping[str, object] | None = None,
        warnings: tuple[str, ...] = (),
        next_actions: tuple[str, ...] = (),
    ) -> "ApplicationResult":
        status = "warning" if warnings else "success"
        return cls(
            status,
            message,
            data or {},
            warnings=warnings,
            next_actions=next_actions,
        )

    @classmethod
    def failure(
        cls,
        message: str,
        *,
        data: Mapping[str, object] | None = None,
        errors: tuple[str, ...] = (),
        warnings: tuple[str, ...] = (),
        next_actions: tuple[str, ...] = (),
    ) -> "ApplicationResult":
        return cls(
            "failure",
            message,
            data or {},
            warnings=warnings,
            errors=errors,
            next_actions=next_actions,
        )

    @property
    def ok(self) -> bool:
        return self.status != "failure"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "message": self.message,
            "data": _thaw_json(self.data),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "next_actions": list(self.next_actions),
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
        )
