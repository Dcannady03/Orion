"""Provider-neutral Image Center schemas, storage, registry, and service."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from orion.services.base import ServiceResult, ServiceState, ServiceStatus


IMAGE_SCHEMA_VERSION = 1
IMAGE_ID_PATTERN = re.compile(r"image-[a-z0-9]{12,48}")
REQUEST_ID_PATTERN = re.compile(r"request-[a-z0-9]{12,48}")
ARTIFACT_ID_PATTERN = re.compile(r"artifact-[a-z0-9]{12,48}")
IMAGE_STATUSES = frozenset({"succeeded", "failed", "unavailable", "rejected"})
IMAGE_SOURCES = frozenset({"cli", "discord", "future"})
IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
IMAGE_FORMATS = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "webp": "image/webp"}
PROVIDER_STATES = frozenset({
    "ready", "disabled", "not_configured", "credential_missing", "client_missing",
    "provider_unavailable", "error",
})
PROTECTED_WORKSPACE_PARTS = frozenset({".git", ".codex", ".agents", ".orion", "vault"})
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|authorization)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}"),
)


def _exact(value: Any, required: set[str], optional: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing:
        raise ValueError(f"{label} is missing required fields: {missing}")
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {unknown}")
    return value


def _text(value: Any, label: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    result = " ".join(value.strip().split())
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    if required and not result:
        raise ValueError(f"{label} cannot be empty.")
    if len(result) > maximum:
        raise ValueError(f"{label} exceeds its {maximum}-character limit.")
    return result


def _identifier(value: Any, pattern: re.Pattern[str], label: str) -> str:
    result = _text(value, label, 100, required=True).lower()
    if pattern.fullmatch(result) is None:
        raise ValueError(f"{label} is invalid.")
    return result


def _timestamp(value: Any, label: str) -> str:
    result = _text(value, label, 80, required=True)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be ISO-8601.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} requires a timezone offset.")
    return result


def _number(value: Any, label: str, *, minimum: float = 0, maximum: float = 86_400) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric.")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f"{label} is outside its safe range.")
    return result


def _optional_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 100_000:
        raise ValueError(f"{label} must be a positive bounded integer.")
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def safe_prompt_preview(value: str, maximum: int = 500) -> str:
    result = " ".join(str(value).strip().split())
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result[:max(int(maximum), 1)]


def _safe_usage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or len(value) > 20:
        raise ValueError("Image provider usage must be a bounded object.")
    result: dict[str, Any] = {}
    for key, item in value.items():
        name = _text(str(key), "Image usage key", 60, required=True)
        if item is None or isinstance(item, (int, float)) and not isinstance(item, bool):
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("Image usage values must be finite.")
            result[name] = item
        elif isinstance(item, str):
            result[name] = _text(item, "Image usage value", 200)
        else:
            raise ValueError("Image usage values must be scalar and bounded.")
    return result


def sniff_image(data: bytes) -> tuple[str, int | None, int | None]:
    """Validate supported image bytes and return MIME type and safe dimensions."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return "image/png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            if marker in {0xD8, 0xD9}:
                offset += 2
                continue
            if offset + 4 > len(data):
                break
            length = int.from_bytes(data[offset + 2:offset + 4], "big")
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and offset + 9 < len(data):
                return "image/jpeg", int.from_bytes(data[offset + 7:offset + 9], "big"), int.from_bytes(data[offset + 5:offset + 7], "big")
            if length < 2:
                break
            offset += 2 + length
        return "image/jpeg", None, None
    if data.startswith(b"RIFF") and len(data) >= 30 and data[8:12] == b"WEBP":
        kind = data[12:16]
        if kind == b"VP8X":
            return "image/webp", 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        return "image/webp", None, None
    raise ValueError("Provider returned unsupported or invalid image content.")


@dataclass(frozen=True)
class ImageGenerationRequest:
    request_id: str
    prompt: str
    negative_prompt: str
    provider: str
    model: str
    count: int
    size: str
    quality: str
    output_format: str
    source_interface: str
    discord_user_id: str
    discord_guild_id: str
    discord_channel_id: str
    created_at: str

    @classmethod
    def from_value(cls, value: Any, *, max_prompt_chars: int = 4_000) -> "ImageGenerationRequest":
        required = {
            "schema_version", "request_id", "prompt", "count", "size", "quality",
            "output_format", "source_interface", "created_at",
        }
        optional = {"negative_prompt", "provider", "model", "discord_user_id", "discord_guild_id", "discord_channel_id"}
        value = _exact(value, required, optional, "Image generation request")
        if value["schema_version"] != IMAGE_SCHEMA_VERSION:
            raise ValueError("Image request schema version is unsupported.")
        count = value["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 10:
            raise ValueError("Image count must be between 1 and 10.")
        source = _text(value["source_interface"], "Image source interface", 20, required=True).lower()
        if source not in IMAGE_SOURCES:
            raise ValueError("Image source interface is unsupported.")
        output_format = _text(value["output_format"], "Image output format", 20, required=True).lower()
        if output_format not in IMAGE_FORMATS:
            raise ValueError("Image output format is unsupported.")
        return cls(
            _identifier(value["request_id"], REQUEST_ID_PATTERN, "Image request ID"),
            _text(value["prompt"], "Image prompt", max_prompt_chars, required=True),
            _text(value.get("negative_prompt", ""), "Negative prompt", max_prompt_chars),
            _text(value.get("provider", ""), "Requested image provider", 50).lower(),
            _text(value.get("model", ""), "Requested image model", 100),
            count,
            _text(value["size"], "Image size", 30, required=True).lower(),
            _text(value["quality"], "Image quality", 30, required=True).lower(),
            output_format,
            source,
            _text(value.get("discord_user_id", ""), "Discord user ID", 30),
            _text(value.get("discord_guild_id", ""), "Discord guild ID", 30),
            _text(value.get("discord_channel_id", ""), "Discord channel ID", 30),
            _timestamp(value["created_at"], "Image request creation time"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": IMAGE_SCHEMA_VERSION, **self.__dict__}


@dataclass(frozen=True)
class ImageArtifact:
    artifact_id: str
    image_id: str
    path: str
    filename: str
    mime_type: str
    byte_size: int
    sha256: str
    width: int | None
    height: int | None
    created_at: str
    provider: str
    model: str

    @classmethod
    def from_value(cls, value: Any) -> "ImageArtifact":
        required = {
            "artifact_id", "image_id", "path", "filename", "mime_type", "byte_size",
            "sha256", "created_at", "provider", "model",
        }
        value = _exact(value, required, {"width", "height"}, "Image artifact")
        path = _text(value["path"], "Image artifact path", 300, required=True).replace("\\", "/")
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0].lower() in {"vault", "tokens"}:
            raise ValueError("Image artifact path must remain inside the external image store.")
        mime = _text(value["mime_type"], "Image MIME type", 50, required=True).lower()
        if mime not in IMAGE_MIME_TYPES:
            raise ValueError("Image artifact MIME type is unsupported.")
        size = value["byte_size"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 1 or size > 1_000_000_000:
            raise ValueError("Image artifact size is invalid.")
        digest = _text(value["sha256"], "Image artifact hash", 64, required=True).lower()
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Image artifact hash is invalid.")
        filename = _text(value["filename"], "Image artifact filename", 100, required=True)
        if Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise ValueError("Image artifact filename is invalid.")
        if relative.name != filename:
            raise ValueError("Image artifact filename does not match its relative path.")
        return cls(
            _identifier(value["artifact_id"], ARTIFACT_ID_PATTERN, "Image artifact ID"),
            _identifier(value["image_id"], IMAGE_ID_PATTERN, "Image ID"),
            path,
            filename,
            mime, size,
            digest,
            _optional_int(value.get("width"), "Image width"),
            _optional_int(value.get("height"), "Image height"),
            _timestamp(value["created_at"], "Image artifact creation time"),
            _text(value["provider"], "Image artifact provider", 50, required=True).lower(),
            _text(value["model"], "Image artifact model", 100, required=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class ImageGenerationResult:
    image_id: str
    request_id: str
    status: str
    requested_provider: str
    resolved_provider: str
    requested_model: str
    resolved_model: str
    fallback_used: bool
    fallback_reason: str
    prompt_summary: str
    revised_prompt: str
    artifacts: tuple[ImageArtifact, ...]
    image_count: int
    mime_type: str
    width: int | None
    height: int | None
    byte_size: int
    sha256: str
    source_interface: str
    discord_user_id: str
    discord_guild_id: str
    discord_channel_id: str
    created_at: str
    completed_at: str
    duration_seconds: float
    usage: Mapping[str, Any]
    estimated_cost_usd: float | None
    safe_error_category: str
    diagnostics: tuple[str, ...]

    @classmethod
    def from_value(cls, value: Any) -> "ImageGenerationResult":
        required = {
            "schema_version", "image_id", "request_id", "status", "requested_provider",
            "resolved_provider", "artifacts", "source_interface", "created_at", "completed_at",
            "duration_seconds", "safe_error_category",
        }
        optional = {
            "requested_model", "resolved_model", "fallback_used", "fallback_reason",
            "prompt_summary", "revised_prompt", "image_count", "mime_type", "width", "height", "byte_size",
            "sha256", "discord_user_id", "discord_guild_id", "discord_channel_id", "usage",
            "estimated_cost_usd", "diagnostics",
        }
        value = _exact(value, required, optional, "Image generation result")
        if value["schema_version"] != IMAGE_SCHEMA_VERSION:
            raise ValueError("Image result schema version is unsupported.")
        status = _text(value["status"], "Image result status", 30, required=True).lower()
        if status not in IMAGE_STATUSES:
            raise ValueError("Image result status is unsupported.")
        if not isinstance(value["artifacts"], list) or len(value["artifacts"]) > 10:
            raise ValueError("Image artifacts must be a bounded array.")
        artifacts = tuple(ImageArtifact.from_value(item) for item in value["artifacts"])
        if status == "succeeded" and not artifacts:
            raise ValueError("Successful image results require an artifact.")
        image_count = value.get("image_count", len(artifacts))
        if isinstance(image_count, bool) or not isinstance(image_count, int) or image_count < 0 or image_count > 10:
            raise ValueError("Image result count is invalid.")
        if image_count != len(artifacts):
            raise ValueError("Image result count does not match its artifacts.")
        if not isinstance(value.get("fallback_used", False), bool):
            raise ValueError("Image fallback flag must be boolean.")
        usage = _safe_usage(value.get("usage", {}))
        cost = value.get("estimated_cost_usd")
        if cost is not None:
            cost = _number(cost, "Estimated image cost", maximum=1_000_000)
        diagnostics_value = value.get("diagnostics", [])
        if not isinstance(diagnostics_value, list) or len(diagnostics_value) > 20:
            raise ValueError("Image diagnostics must be a bounded array.")
        source = _text(value["source_interface"], "Image source interface", 20, required=True).lower()
        if source not in IMAGE_SOURCES:
            raise ValueError("Image source interface is unsupported.")
        size = value.get("byte_size", 0)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > 1_000_000_000:
            raise ValueError("Image result byte size is invalid.")
        mime = _text(value.get("mime_type", ""), "Image MIME type", 50).lower()
        if mime and mime not in IMAGE_MIME_TYPES:
            raise ValueError("Image result MIME type is unsupported.")
        digest = _text(value.get("sha256", ""), "Image hash", 64).lower()
        if digest and re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Image result hash is invalid.")
        if artifacts:
            if mime and mime != artifacts[0].mime_type:
                raise ValueError("Image result MIME type does not match its primary artifact.")
            if digest and digest != artifacts[0].sha256:
                raise ValueError("Image result hash does not match its primary artifact.")
            if size and size != sum(item.byte_size for item in artifacts):
                raise ValueError("Image result byte size does not match its artifacts.")
        return cls(
            _identifier(value["image_id"], IMAGE_ID_PATTERN, "Image ID"),
            _identifier(value["request_id"], REQUEST_ID_PATTERN, "Image request ID"), status,
            _text(value["requested_provider"], "Requested provider", 50).lower(),
            _text(value["resolved_provider"], "Resolved provider", 50).lower(),
            _text(value.get("requested_model", ""), "Requested model", 100),
            _text(value.get("resolved_model", ""), "Resolved model", 100),
            value.get("fallback_used", False), _text(value.get("fallback_reason", ""), "Fallback reason", 500),
            _text(value.get("prompt_summary", ""), "Prompt summary", 500),
            _text(value.get("revised_prompt", ""), "Revised prompt", 1_000), artifacts, image_count,
            mime,
            _optional_int(value.get("width"), "Image width"), _optional_int(value.get("height"), "Image height"),
            size, digest, source,
            _text(value.get("discord_user_id", ""), "Discord user ID", 30),
            _text(value.get("discord_guild_id", ""), "Discord guild ID", 30),
            _text(value.get("discord_channel_id", ""), "Discord channel ID", 30),
            _timestamp(value["created_at"], "Image creation time"),
            _timestamp(value["completed_at"], "Image completion time"),
            _number(value["duration_seconds"], "Image duration"), usage, cost,
            _text(value["safe_error_category"], "Image error category", 100),
            tuple(_text(item, "Image diagnostic", 500, required=True) for item in diagnostics_value),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": IMAGE_SCHEMA_VERSION,
            **{key: value for key, value in self.__dict__.items() if key not in {"artifacts", "diagnostics", "usage"}},
            "artifacts": [item.to_dict() for item in self.artifacts],
            "usage": dict(self.usage),
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class ImageProviderStatus:
    provider: str
    display_name: str
    state: str
    detail: str
    model: str
    formats: tuple[str, ...] = ("png",)
    sizes: tuple[str, ...] = ("1024x1024",)
    max_count: int = 1

    def __post_init__(self) -> None:
        if self.state not in PROVIDER_STATES:
            raise ValueError("Image provider state is unsupported.")

    @property
    def ready(self) -> bool:
        return self.state == "ready"


@dataclass(frozen=True)
class ProviderImage:
    data: bytes
    mime_type: str
    revised_prompt: str = ""


@dataclass(frozen=True)
class ImageProviderOutput:
    provider: str
    model: str
    images: tuple[ProviderImage, ...]
    usage: Mapping[str, Any]
    estimated_cost_usd: float | None = None


class ImageProviderError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(_text(message, "Image provider error", 500, required=True))
        self.category = _text(category, "Image provider error category", 100, required=True).lower()


class ImageProviderAdapter(Protocol):
    @property
    def name(self) -> str: ...
    def status(self) -> ImageProviderStatus: ...
    def generate(self, request: ImageGenerationRequest) -> ImageProviderOutput: ...


class ImageProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ImageProviderAdapter] = {}
        self._lock = threading.RLock()

    def register(self, adapter: ImageProviderAdapter, *, replace: bool = False) -> None:
        name = _text(adapter.name, "Image provider name", 50, required=True).lower()
        with self._lock:
            if name in self._providers and not replace:
                raise KeyError(f"Image provider is already registered: {name}")
            self._providers[name] = adapter

    def statuses(self) -> tuple[ImageProviderStatus, ...]:
        with self._lock:
            providers = tuple(self._providers.values())
        values: list[ImageProviderStatus] = []
        for provider in providers:
            try:
                values.append(provider.status())
            except Exception:
                values.append(ImageProviderStatus(provider.name, provider.name.title(), "error", "Readiness check failed safely.", ""))
        return tuple(values)

    def resolve(self, requested: str, *, allow_fallback: bool) -> tuple[ImageProviderAdapter, ImageProviderStatus, bool, str]:
        normalized = _text(requested, "Image provider", 50, required=True).lower()
        with self._lock:
            selected = self._providers.get(normalized)
            providers = tuple(self._providers.values())
        if selected is None:
            raise ImageProviderError("unknown_provider", f"Unknown image provider: {normalized}")
        status = selected.status()
        if status.ready:
            return selected, status, False, ""
        if allow_fallback:
            for candidate in providers:
                candidate_status = candidate.status()
                if candidate_status.ready:
                    return candidate, candidate_status, True, f"{normalized} was {status.state}; selected {candidate.name}."
        raise ImageProviderError(status.state, status.detail or f"{status.display_name} is unavailable.")


class ImageStore:
    """External immutable image artifacts with a bounded, recoverable index."""
    def __init__(self, root: str | Path, *, history_limit: int = 100, now: Callable[[], str] = utc_now):
        self.root = Path(root).expanduser().resolve()
        self.history_limit = max(1, min(int(history_limit), 10_000))
        self._now = now
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self._owner_only(self.root)

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    @staticmethod
    def _owner_only(path: Path) -> None:
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if path.is_dir() else 0))
        except OSError:
            pass

    def _contained(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Image store path escapes the external image root.") from exc
        return resolved

    def _atomic_json(self, path: Path, value: Any) -> None:
        self._atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")

    def _atomic_text(self, path: Path, value: str) -> None:
        target = self._contained(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        temporary.write_text(value, encoding="utf-8")
        self._owner_only(temporary)
        temporary.replace(target)
        self._owner_only(target)

    def _read_index(self) -> list[dict[str, str]]:
        if not self.index_path.is_file():
            return []
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("schema_version") != IMAGE_SCHEMA_VERSION or not isinstance(value.get("entries"), list):
                return []
            entries: list[dict[str, str]] = []
            for item in value["entries"][:self.history_limit]:
                if isinstance(item, dict) and IMAGE_ID_PATTERN.fullmatch(str(item.get("image_id", ""))) and isinstance(item.get("result_path"), str):
                    entries.append({"image_id": item["image_id"], "result_path": item["result_path"]})
            return entries
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []

    def _write_index(self, entries: list[dict[str, str]]) -> None:
        self._atomic_json(self.index_path, {
            "schema_version": IMAGE_SCHEMA_VERSION,
            "updated_at": self._now(),
            "entries": entries[:self.history_limit],
        })

    def _recover_entries(self) -> list[dict[str, str]]:
        recovered: list[tuple[str, str, str]] = []
        for path in self.root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/image-*/result.json"):
            try:
                result = ImageGenerationResult.from_value(json.loads(path.read_text(encoding="utf-8")))
                recovered.append((result.created_at, result.image_id, path.relative_to(self.root).as_posix()))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                continue
        recovered.sort(reverse=True)
        entries = [
            {"image_id": image_id, "result_path": result_path}
            for _, image_id, result_path in recovered[:self.history_limit]
        ]
        if entries:
            self._write_index(entries)
        return entries

    def persist(self, result: ImageGenerationResult, images: tuple[ProviderImage, ...]) -> ImageGenerationResult:
        with self._lock:
            created = datetime.fromisoformat(result.created_at.replace("Z", "+00:00"))
            directory = self._contained(self.root / f"{created.year:04d}" / f"{created.month:02d}" / result.image_id)
            if directory.exists():
                raise FileExistsError(f"Image artifact already exists: {result.image_id}")
            directory.mkdir(parents=True, exist_ok=False)
            self._owner_only(directory)
            artifacts: list[ImageArtifact] = []
            try:
                for index, image in enumerate(images, start=1):
                    mime, width, height = sniff_image(image.data)
                    if image.mime_type and image.mime_type != mime:
                        raise ValueError("Image content does not match its declared media type.")
                    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
                    filename = f"image-{index:03d}.{extension}"
                    path = directory / filename
                    with path.open("xb") as handle:
                        handle.write(image.data)
                    self._owner_only(path)
                    digest = hashlib.sha256(image.data).hexdigest()
                    artifacts.append(ImageArtifact(
                        f"artifact-{uuid4().hex[:20]}", result.image_id,
                        path.relative_to(self.root).as_posix(), filename, mime, len(image.data), digest,
                        width, height, result.completed_at, result.resolved_provider, result.resolved_model,
                    ))
                if result.status == "succeeded" and not artifacts:
                    raise ValueError("Successful generation did not return an image.")
                first = artifacts[0] if artifacts else None
                saved = ImageGenerationResult(
                    **{
                        **result.__dict__,
                        "artifacts": tuple(artifacts),
                        "image_count": len(artifacts),
                        "mime_type": first.mime_type if first else "",
                        "width": first.width if first else None,
                        "height": first.height if first else None,
                        "byte_size": sum(item.byte_size for item in artifacts),
                        "sha256": first.sha256 if first else "",
                    }
                )
                ImageGenerationResult.from_value(saved.to_dict())
                self._atomic_json(directory / "result.json", saved.to_dict())
                log = "\n".join((
                    f"Image ID: {saved.image_id}", f"Status: {saved.status}",
                    f"Provider: {saved.resolved_provider or saved.requested_provider}",
                    f"Model: {saved.resolved_model or saved.requested_model}",
                    f"Artifacts: {len(saved.artifacts)}", f"Error category: {saved.safe_error_category or 'none'}",
                ))[:16_000] + "\n"
                (directory / "generation.log").write_text(log, encoding="utf-8")
                self._owner_only(directory / "generation.log")
                entries = [item for item in self._read_index() if item["image_id"] != saved.image_id]
                entries.insert(0, {"image_id": saved.image_id, "result_path": (directory / "result.json").relative_to(self.root).as_posix()})
                self._write_index(entries)
                return saved
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise

    def load(self, image_id: str) -> ImageGenerationResult:
        normalized = _identifier(image_id, IMAGE_ID_PATTERN, "Image ID")
        with self._lock:
            for entry in self._read_index():
                if entry["image_id"] != normalized:
                    continue
                path = self._contained(self.root / entry["result_path"])
                try:
                    return ImageGenerationResult.from_value(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                    break
            for path in self.root.glob(f"[0-9][0-9][0-9][0-9]/[0-9][0-9]/{normalized}/result.json"):
                try:
                    return ImageGenerationResult.from_value(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                    continue
        raise FileNotFoundError(f"Unknown or corrupt image: {normalized}")

    def history(self, limit: int = 10) -> tuple[ImageGenerationResult, ...]:
        values: list[ImageGenerationResult] = []
        entries = self._read_index()
        if not entries:
            entries = self._recover_entries()
        for entry in entries[:max(1, min(int(limit), self.history_limit))]:
            try:
                values.append(self.load(entry["image_id"]))
            except (FileNotFoundError, ValueError):
                continue
        return tuple(values)

    def artifact_path(self, artifact: ImageArtifact, *, verify: bool = True) -> Path:
        path = self._contained(self.root / artifact.path)
        if not path.is_file():
            raise FileNotFoundError("Image artifact is missing.")
        if verify:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != artifact.sha256 or path.stat().st_size != artifact.byte_size:
                raise ValueError("Image artifact integrity check failed.")
        return path

    def record_event(self, image_id: str, event: str, metadata: Mapping[str, Any]) -> None:
        result = self.load(image_id)
        directory = self._contained((self.root / result.artifacts[0].path).parent if result.artifacts else next(
            self.root.glob(f"[0-9][0-9][0-9][0-9]/[0-9][0-9]/{result.image_id}"), self.root
        ))
        safe = {
            "schema_version": IMAGE_SCHEMA_VERSION,
            "timestamp": self._now(),
            "event": _text(event, "Image event", 60, required=True),
            "image_id": result.image_id,
            "metadata": {str(key)[:60]: _text(str(value), "Image event value", 300) for key, value in list(metadata.items())[:20]},
        }
        path = directory / "events.jsonl"
        encoded = json.dumps(safe, ensure_ascii=False)
        with self._lock:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()[-99:] if path.is_file() else []
            except (OSError, UnicodeError):
                lines = []
            lines.append(encoded)
            while len(lines) > 1 and len(("\n".join(lines) + "\n").encode("utf-8")) > 65_536:
                lines.pop(0)
            self._atomic_text(path, "\n".join(lines) + "\n")


@dataclass(frozen=True)
class ImageSavePlan:
    image_id: str
    source: str
    destination: str
    byte_size: int
    sha256: str
    exists: bool


class ImageService:
    name = "image"

    def __init__(
        self,
        config_manager,
        registry: ImageProviderRegistry,
        store: ImageStore,
        workspace: str | Path,
        *,
        request_id_factory: Callable[[], str] | None = None,
        image_id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] = utc_now,
    ) -> None:
        self.config = config_manager
        self.registry = registry
        self.store = store
        self.workspace = Path(workspace).expanduser().resolve()
        self._request_id_factory = request_id_factory or (lambda: f"request-{uuid4().hex[:20]}")
        self._image_id_factory = image_id_factory or (lambda: f"image-{uuid4().hex[:20]}")
        self._now = now
        concurrent = max(1, min(int(self.config.get("image.max_concurrent_jobs", 2)), 16))
        self._jobs = threading.BoundedSemaphore(concurrent)

    def bind(self, workspace: str | Path) -> None:
        root = Path(workspace).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {root}")
        self.workspace = root

    def is_available(self) -> bool:
        return self.get_status().available

    def get_status(self) -> ServiceStatus:
        if not bool(self.config.get("image.enabled", True)):
            return ServiceStatus(ServiceState.UNAVAILABLE, "Image Center is disabled")
        provider = str(self.config.get("image.provider", "openai")).strip().lower()
        status = next((item for item in self.registry.statuses() if item.provider == provider), None)
        if status is None:
            return ServiceStatus(ServiceState.UNAVAILABLE, f"Unknown image provider: {provider}")
        state = ServiceState.AVAILABLE if status.ready else ServiceState.DEGRADED
        return ServiceStatus(state, f"{status.display_name}: {status.state.replace('_', ' ').title()}")

    def handle_request(self, request: str) -> ServiceResult:
        result = self.generate(request, source_interface="future")
        return ServiceResult(result.status == "succeeded", data=result.to_dict(), error=result.safe_error_category)

    def statuses(self) -> tuple[ImageProviderStatus, ...]:
        return self.registry.statuses()

    def set_provider(self, provider: str) -> ImageProviderStatus:
        adapter, status, _, _ = self.registry.resolve(provider, allow_fallback=False)
        self.config.set("image.provider", adapter.name)
        self.config.save()
        return status

    def _request(
        self, prompt: str, *, provider: str = "", model: str = "", source_interface: str = "cli",
        discord_user_id: str = "", discord_guild_id: str = "", discord_channel_id: str = "",
    ) -> ImageGenerationRequest:
        maximum = max(1, min(int(self.config.get("image.max_prompt_chars", 4_000)), 20_000))
        default_provider = str(self.config.get("image.provider", "openai")).strip().lower()
        value = {
            "schema_version": IMAGE_SCHEMA_VERSION,
            "request_id": self._request_id_factory(), "prompt": prompt, "negative_prompt": "",
            "provider": provider.strip().lower(), "model": model.strip(),
            "count": 1, "size": str(self.config.get("image.openai.size", "1024x1024")),
            "quality": str(self.config.get("image.openai.quality", "medium")),
            "output_format": str(self.config.get("image.output_format", "png")),
            "source_interface": source_interface, "discord_user_id": discord_user_id,
            "discord_guild_id": discord_guild_id, "discord_channel_id": discord_channel_id,
            "created_at": self._now(),
        }
        request = ImageGenerationRequest.from_value(value, max_prompt_chars=maximum)
        self._moderate(request.prompt)
        if request.count > int(self.config.get("image.max_images_per_request", 1)):
            raise ValueError("Requested image count exceeds the configured limit.")
        if not request.provider:
            request = ImageGenerationRequest(**{**request.__dict__, "provider": default_provider})
        return request

    @staticmethod
    def _moderate(prompt: str) -> None:
        value = prompt.casefold()
        minor = any(term in value for term in ("child", "minor", "underage", "kid", "toddler"))
        sexual = any(term in value for term in ("sexual", "nude", "naked", "porn", "explicit"))
        if minor and sexual:
            raise ImageProviderError("content_rejected", "The image request is not allowed.")
        if any(term in value for term in ("show me the api key", "render the access token", "draw the password", "expose the secret")):
            raise ImageProviderError("content_rejected", "Secret-exfiltration image requests are not allowed.")
        if "file://" in value or re.search(r"\b[a-z]:\\(?:users|windows)\\", value) or "/home/" in value:
            raise ImageProviderError("content_rejected", "Image Center cannot read private local files.")

    def generate(self, prompt: str, **metadata: Any) -> ImageGenerationResult:
        created = self._now()
        image_id = self._image_id_factory()
        started = time.monotonic()
        request: ImageGenerationRequest | None = None
        images: tuple[ProviderImage, ...] = ()
        try:
            if not bool(self.config.get("image.enabled", True)):
                raise ImageProviderError("disabled", "Image Center is disabled.")
            request = self._request(prompt, **metadata)
            if not self._jobs.acquire(blocking=False):
                raise ImageProviderError("busy", "Image Center has reached its concurrent-job limit.")
            try:
                explicit = bool(str(metadata.get("provider", "")).strip())
                adapter, provider_status, fallback, fallback_reason = self.registry.resolve(
                    request.provider, allow_fallback=not explicit,
                )
                output = adapter.generate(request)
            finally:
                self._jobs.release()
            max_bytes = max(1, int(self.config.get("image.max_image_bytes", 20_971_520)))
            if len(output.images) < 1 or len(output.images) > int(self.config.get("image.max_images_per_request", 1)):
                raise ImageProviderError("invalid_response", "Provider returned an unexpected image count.")
            if any(len(item.data) > max_bytes for item in output.images):
                raise ImageProviderError("response_too_large", "Generated image exceeded the configured byte limit.")
            images = output.images
            result = ImageGenerationResult(
                image_id, request.request_id, "succeeded", request.provider, output.provider,
                request.model, output.model, fallback, fallback_reason,
                safe_prompt_preview(request.prompt, int(self.config.get("image.prompt_preview_chars", 300))),
                safe_prompt_preview(images[0].revised_prompt, 1_000) if images else "", (), 0, "", None, None, 0, "",
                request.source_interface, request.discord_user_id, request.discord_guild_id,
                request.discord_channel_id, request.created_at, self._now(), time.monotonic() - started,
                _safe_usage(output.usage), output.estimated_cost_usd, "", (),
            )
            return self.store.persist(result, images)
        except Exception as exc:
            category = exc.category if isinstance(exc, ImageProviderError) else "generation_failed"
            status = "rejected" if category == "content_rejected" else ("unavailable" if category in PROVIDER_STATES or category in {"disabled", "unknown_provider"} else "failed")
            request_id = request.request_id if request else self._request_id_factory()
            source = request.source_interface if request else str(metadata.get("source_interface", "cli"))
            requested = request.provider if request else str(metadata.get("provider", self.config.get("image.provider", "openai")))
            result = ImageGenerationResult(
                image_id, request_id, status, requested, "", request.model if request else "", "", False, "",
                safe_prompt_preview(prompt, int(self.config.get("image.prompt_preview_chars", 300))), "", (), 0, "", None, None, 0, "",
                source if source in IMAGE_SOURCES else "future", str(metadata.get("discord_user_id", "")),
                str(metadata.get("discord_guild_id", "")), str(metadata.get("discord_channel_id", "")),
                request.created_at if request else created, self._now(), time.monotonic() - started, {}, None,
                category, (f"Generation stopped safely ({type(exc).__name__}).",),
            )
            return self.store.persist(result, ())

    def history(self, limit: int = 10) -> tuple[ImageGenerationResult, ...]:
        return self.store.history(limit)

    def show(self, image_id: str) -> ImageGenerationResult:
        return self.store.load(image_id)

    def prepare_save(self, image_id: str, destination: str) -> ImageSavePlan:
        result = self.store.load(image_id)
        if result.status != "succeeded" or not result.artifacts:
            raise ValueError("Only successful generated images can be saved.")
        value = Path(str(destination).strip())
        if value.is_absolute() or not value.parts or ".." in value.parts:
            raise ValueError("Image destination must be a workspace-relative path.")
        if any(part.casefold() in PROTECTED_WORKSPACE_PARTS for part in value.parts):
            raise ValueError("Image destination cannot use protected workspace metadata.")
        target = (self.workspace / value).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("Image destination escapes the active workspace.") from exc
        parent = target.parent
        while parent != self.workspace:
            if parent.exists() and parent.is_symlink():
                raise ValueError("Image destination cannot traverse a symlink.")
            parent = parent.parent
        artifact = result.artifacts[0]
        source = self.store.artifact_path(artifact)
        return ImageSavePlan(result.image_id, str(source), value.as_posix(), artifact.byte_size, artifact.sha256, target.exists())

    def save(self, image_id: str, destination: str) -> Path:
        plan = self.prepare_save(image_id, destination)
        if plan.exists:
            raise FileExistsError("Image destination already exists; Orion never overwrites files.")
        source = Path(plan.source)
        target = (self.workspace / plan.destination).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        created_target = False
        try:
            with source.open("rb") as reader, target.open("xb") as writer:
                created_target = True
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest != plan.sha256:
                target.unlink(missing_ok=True)
                created_target = False
                raise ValueError("Copied image failed its integrity check.")
        except Exception:
            if created_target and target.exists():
                target.unlink(missing_ok=True)
            raise
        self.store.record_event(image_id, "workspace_saved", {"destination": plan.destination, "sha256": plan.sha256})
        return target

    def record_delivery(self, image_id: str, *, succeeded: bool, byte_size: int, category: str = "") -> None:
        self.store.record_event(image_id, "discord_delivery", {
            "succeeded": succeeded, "byte_size": byte_size, "category": category,
        })
