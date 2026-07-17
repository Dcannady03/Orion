"""Persistent, user-owned AI provider performance telemetry."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading


class AIPerformanceStore:
    """Record bounded routing outcomes without storing prompts or responses."""

    MAX_RECENT_OUTCOMES = 100

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser().resolve() if path else None
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, object]] = {}
        self._load()

    @staticmethod
    def _key(provider: str, model: str) -> str:
        return f"{provider.strip().lower()}:{model.strip()}"

    @staticmethod
    def _safe_error(error: object) -> str:
        """Store a compact error category without request or response content."""
        if not error:
            return ""
        if isinstance(error, BaseException):
            return type(error).__name__
        text = str(error).strip()
        return text.split(":", 1)[0][:80] or "ProviderError"

    def record(self, provider: str, model: str, duration_seconds: float, success: bool,
               error: object = "") -> None:
        with self._lock:
            provider_key = provider.strip().lower()
            model_key = model.strip() or "unknown"
            key = self._key(provider_key, model_key)
            item = self._records.setdefault(key, {
                "provider": provider_key, "model": model_key, "recent": [],
                "last_duration_seconds": 0.0, "last_success": None,
                "last_error": "", "updated_at": "",
            })
            recent = item.setdefault("recent", [])
            if not isinstance(recent, list):
                recent = []
                item["recent"] = recent
            recent.append({
                "success": bool(success),
                "duration_seconds": round(max(0.0, float(duration_seconds)), 3),
            })
            del recent[:-self.MAX_RECENT_OUTCOMES]
            item["last_duration_seconds"] = round(max(0.0, float(duration_seconds)), 3)
            item["last_success"] = bool(success)
            item["last_error"] = "" if success else self._safe_error(error)
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()

    def summary(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            rows = []
            for item in self._records.values():
                recent = item.get("recent", [])
                if not isinstance(recent, list):
                    continue
                valid = [entry for entry in recent if isinstance(entry, dict)]
                requests = len(valid)
                successes = sum(1 for entry in valid if bool(entry.get("success")))
                duration = sum(max(0.0, float(entry.get("duration_seconds", 0.0))) for entry in valid)
                row = dict(item)
                row.update({
                    "requests": requests,
                    "successes": successes,
                    "failures": requests - successes,
                    "total_duration_seconds": round(duration, 3),
                    "success_rate_percent": round(successes / requests * 100, 1) if requests else 0.0,
                    "average_duration_seconds": round(duration / requests, 3) if requests else 0.0,
                })
                rows.append(row)
            return tuple(sorted(rows, key=lambda row: (str(row["provider"]), str(row["model"]))))

    def provider_health(self, provider: str, *, model: str | None = None,
                        minimum_samples: int = 3) -> dict[str, object]:
        provider_key = provider.strip().lower()
        rows = [row for row in self.summary() if row["provider"] == provider_key]
        if model is not None:
            model_key = model.strip() or "unknown"
            model_rows = [row for row in rows if row["model"] == model_key]
            if model_rows:
                rows = model_rows
            else:
                rows = []
        requests = sum(int(row["requests"]) for row in rows)
        successes = sum(int(row["successes"]) for row in rows)
        duration = sum(float(row["total_duration_seconds"]) for row in rows)
        rate = successes / requests if requests else 1.0
        if requests < max(1, minimum_samples):
            state = "learning"
        elif rate >= 0.8:
            state = "healthy"
        elif rate >= 0.5:
            state = "degraded"
        else:
            state = "unhealthy"
        return {
            "provider": provider_key, "model": model or "all", "state": state,
            "requests": requests, "success_rate_percent": round(rate * 100, 1),
            "average_duration_seconds": round(duration / requests, 3) if requests else 0.0,
        }

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._save()

    def _normalize_record(self, key: str, value: object) -> dict[str, object] | None:
        if not isinstance(value, dict):
            return None
        provider = str(value.get("provider", key.split(":", 1)[0])).strip().lower()
        model = str(value.get("model", key.split(":", 1)[1] if ":" in key else "unknown")).strip()
        if not provider or not model:
            return None
        recent = value.get("recent")
        normalized_recent: list[dict[str, object]] = []
        if isinstance(recent, list):
            for entry in recent[-self.MAX_RECENT_OUTCOMES:]:
                if not isinstance(entry, dict):
                    continue
                try:
                    normalized_recent.append({
                        "success": bool(entry.get("success")),
                        "duration_seconds": round(max(0.0, float(entry.get("duration_seconds", 0.0))), 3),
                    })
                except (TypeError, ValueError):
                    continue
        else:
            # Migrate the v0.5.4 aggregate schema into a bounded synthetic history.
            try:
                requests = min(max(0, int(value.get("requests", 0))), self.MAX_RECENT_OUTCOMES)
                successes = min(max(0, int(value.get("successes", 0))), requests)
                average = max(0.0, float(value.get("total_duration_seconds", 0.0))) / requests if requests else 0.0
                normalized_recent.extend({"success": True, "duration_seconds": round(average, 3)} for _ in range(successes))
                normalized_recent.extend({"success": False, "duration_seconds": round(average, 3)} for _ in range(requests - successes))
            except (TypeError, ValueError):
                normalized_recent = []
        return {
            "provider": provider, "model": model, "recent": normalized_recent,
            "last_duration_seconds": float(value.get("last_duration_seconds", 0.0) or 0.0),
            "last_success": value.get("last_success") if isinstance(value.get("last_success"), bool) else None,
            "last_error": self._safe_error(value.get("last_error", "")),
            "updated_at": str(value.get("updated_at", "")),
        }

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                normalized = {}
                for key, value in payload.items():
                    item = self._normalize_record(str(key), value)
                    if item is not None:
                        normalized[self._key(str(item["provider"]), str(item["model"]))] = item
                self._records = normalized
        except (OSError, ValueError):
            self._records = {}

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(self._records, indent=2, sort_keys=True), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            # Observability must never make a successful AI request fail.
            return
