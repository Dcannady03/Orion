"""Persistent, user-owned AI provider performance telemetry."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading


class AIPerformanceStore:
    """Record aggregate routing outcomes without storing prompts or responses."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser().resolve() if path else None
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, object]] = {}
        self._load()

    @staticmethod
    def _key(provider: str, model: str) -> str:
        return f"{provider.strip().lower()}:{model.strip()}"

    def record(self, provider: str, model: str, duration_seconds: float, success: bool,
               error: str = "") -> None:
        with self._lock:
            key = self._key(provider, model)
            item = self._records.setdefault(key, {
                "provider": provider.lower(), "model": model, "requests": 0,
                "successes": 0, "failures": 0, "total_duration_seconds": 0.0,
                "last_duration_seconds": 0.0, "last_success": None,
                "last_error": "", "updated_at": "",
            })
            item["requests"] = int(item["requests"]) + 1
            item["successes" if success else "failures"] = int(item["successes" if success else "failures"]) + 1
            item["total_duration_seconds"] = float(item["total_duration_seconds"]) + max(0.0, duration_seconds)
            item["last_duration_seconds"] = round(max(0.0, duration_seconds), 3)
            item["last_success"] = bool(success)
            item["last_error"] = "" if success else str(error)[:500]
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()

    def summary(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            rows = []
            for item in self._records.values():
                row = dict(item)
                requests = int(row["requests"])
                successes = int(row["successes"])
                row["success_rate_percent"] = round(successes / requests * 100, 1) if requests else 0.0
                row["average_duration_seconds"] = round(float(row["total_duration_seconds"]) / requests, 3) if requests else 0.0
                rows.append(row)
            return tuple(sorted(rows, key=lambda row: (str(row["provider"]), str(row["model"]))))

    def provider_health(self, provider: str, *, minimum_samples: int = 3) -> dict[str, object]:
        rows = [row for row in self.summary() if row["provider"] == provider.lower()]
        requests = sum(int(row["requests"]) for row in rows)
        successes = sum(int(row["successes"]) for row in rows)
        duration = sum(float(row["total_duration_seconds"]) for row in rows)
        rate = successes / requests if requests else 1.0
        if requests < minimum_samples:
            state = "learning"
        elif rate >= 0.8:
            state = "healthy"
        elif rate >= 0.5:
            state = "degraded"
        else:
            state = "unhealthy"
        return {"provider": provider.lower(), "state": state, "requests": requests,
                "success_rate_percent": round(rate * 100, 1),
                "average_duration_seconds": round(duration / requests, 3) if requests else 0.0}

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._save()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._records = {str(key): value for key, value in payload.items() if isinstance(value, dict)}
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
