"""Monitor the local gateway and public Internet connectivity."""
from __future__ import annotations

from datetime import datetime
import json
import platform
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Callable

from orion.plugins.base import OrionPlugin, PluginContext


DEFAULT_TARGETS = {"Router": "10.0.0.1", "Cloudflare": "1.1.1.1", "Google": "8.8.8.8"}


class PingResult:
    def __init__(self, name: str, host: str, online: bool, latency_ms: float | None,
                 checked_at: str, error: str = ""):
        self.name, self.host, self.online = name, host, online
        self.latency_ms, self.checked_at, self.error = latency_ms, checked_at, error

    def as_dict(self) -> dict[str, object]:
        return vars(self).copy()


class TargetStats:
    def __init__(self):
        self.checks = self.failures = self.latency_spikes = self.successful_checks = 0
        self.total_latency_ms = self.highest_latency_ms = self.total_offline_seconds = 0.0
        self.outages = 0
        self.current_online: bool | None = None
        self.offline_since: float | None = None


class NetworkMonitor:
    """Thread-safe background monitor with JSON Lines event logging."""

    def __init__(self, log_directory: str | Path, *, targets: dict[str, str] | None = None,
                 interval_seconds: float = 5.0, timeout_ms: int = 2000,
                 latency_warning_ms: float = 100.0,
                 ping_runner: Callable[[str, int], tuple[bool, float | None, str]] | None = None):
        self.log_directory = Path(log_directory).expanduser().resolve()
        self.targets = dict(targets or DEFAULT_TARGETS)
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.timeout_ms = max(250, int(timeout_ms))
        self.latency_warning_ms = max(1.0, float(latency_warning_ms))
        self._ping_runner = ping_runner or self._system_ping
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at: datetime | None = None
        self._log_path: Path | None = None
        self._stats = {name: TargetStats() for name in self.targets}
        self._last_results: dict[str, PingResult] = {}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def log_path(self) -> Path | None:
        return self._log_path

    def check_once(self) -> tuple[PingResult, ...]:
        checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
        results = []
        for name, host in self.targets.items():
            online, latency_ms, error = self._ping_runner(host, self.timeout_ms)
            results.append(PingResult(name, host, online, latency_ms, checked_at, error))
        return tuple(results)

    def start(self, interval_seconds: float | None = None) -> bool:
        with self._lock:
            if self.running:
                return False
            if interval_seconds is not None:
                self.interval_seconds = max(1.0, float(interval_seconds))
            self.log_directory.mkdir(parents=True, exist_ok=True)
            self._started_at = datetime.now().astimezone()
            self._log_path = self.log_directory / f"network-{self._started_at:%Y%m%d-%H%M%S}.jsonl"
            self._stats = {name: TargetStats() for name in self.targets}
            self._last_results.clear()
            self._stop_event.clear()
            self._write_event({"event": "monitor_started", "timestamp": self._started_at.isoformat(),
                               "interval_seconds": self.interval_seconds, "targets": self.targets})
            self._thread = threading.Thread(target=self._monitor_loop,
                                            name="orion-network-watch", daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout: float = 10.0) -> bool:
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return False
            self._stop_event.set()
        thread.join(timeout)
        if thread.is_alive():
            return False
        with self._lock:
            self._close_outages()
            self._write_event({"event": "monitor_stopped", "timestamp": datetime.now().astimezone().isoformat(),
                               "summary": self.summary()})
        return True

    def summary(self) -> dict[str, object]:
        with self._lock:
            targets = {}
            now = time.monotonic()
            for name, stats in self._stats.items():
                offline = stats.total_offline_seconds
                if stats.current_online is False and stats.offline_since is not None:
                    offline += max(0.0, now - stats.offline_since)
                average = stats.total_latency_ms / stats.successful_checks if stats.successful_checks else 0.0
                loss = stats.failures / stats.checks * 100 if stats.checks else 0.0
                targets[name] = {"host": self.targets[name], "checks": stats.checks,
                                 "failures": stats.failures, "packet_loss_percent": round(loss, 2),
                                 "outages": stats.outages, "offline_seconds": round(offline, 1),
                                 "average_latency_ms": round(average, 1),
                                 "highest_latency_ms": round(stats.highest_latency_ms, 1),
                                 "latency_spikes": stats.latency_spikes, "online": stats.current_online}
            return {"running": self.running,
                    "started_at": self._started_at.isoformat(timespec="seconds") if self._started_at else None,
                    "log_path": str(self._log_path) if self._log_path else None,
                    "diagnosis": self.diagnosis(), "targets": targets}

    def diagnosis(self) -> str:
        router = self._stats.get("Router")
        public = [stats for name, stats in self._stats.items() if name != "Router"]
        if router is None or router.current_online is None:
            return "Not enough data yet."
        if router.current_online is False:
            return "Local gateway is unreachable. Check Ethernet, Wi-Fi, or the router."
        if public and all(stats.current_online is False for stats in public):
            return "Local network is online, but the Internet is unreachable. Likely modem or ISP issue."
        if public and any(stats.current_online is False for stats in public):
            return "Internet is reachable, but one public target failed."
        if any(stats.latency_spikes for stats in self._stats.values()):
            return "Connection is online, but latency spikes were detected."
        return "Local network and Internet targets are online."

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            for result in self.check_once():
                self._record_result(result)
            self._stop_event.wait(max(0.1, self.interval_seconds - (time.monotonic() - started)))

    def _record_result(self, result: PingResult) -> None:
        with self._lock:
            stats = self._stats[result.name]
            previous = stats.current_online
            stats.checks += 1
            stats.current_online = result.online
            self._last_results[result.name] = result
            event = "check"
            if result.online:
                stats.successful_checks += 1
                latency = float(result.latency_ms or 0.0)
                stats.total_latency_ms += latency
                stats.highest_latency_ms = max(stats.highest_latency_ms, latency)
                if latency >= self.latency_warning_ms:
                    stats.latency_spikes += 1
                    event = "latency_spike"
                if previous is False:
                    event = "restored"
                    if stats.offline_since is not None:
                        stats.total_offline_seconds += time.monotonic() - stats.offline_since
                    stats.offline_since = None
            else:
                stats.failures += 1
                if previous is not False:
                    event = "outage_started"
                    stats.outages += 1
                    stats.offline_since = time.monotonic()
            self._write_event({"event": event, **result.as_dict()})

    def _close_outages(self) -> None:
        now = time.monotonic()
        for stats in self._stats.values():
            if stats.current_online is False and stats.offline_since is not None:
                stats.total_offline_seconds += max(0.0, now - stats.offline_since)
                stats.offline_since = None

    def _write_event(self, payload: dict[str, object]) -> None:
        if self._log_path:
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")

    @staticmethod
    def _system_ping(host: str, timeout_ms: int) -> tuple[bool, float | None, str]:
        windows = platform.system().lower() == "windows"
        command = (["ping", "-n", "1", "-w", str(timeout_ms), host] if windows else
                   ["ping", "-c", "1", "-W", str(max(1, round(timeout_ms / 1000))), host])
        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False,
                                       timeout=timeout_ms / 1000 + 2,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if windows else 0)
        except subprocess.TimeoutExpired:
            return False, None, "timeout"
        except OSError as exc:
            return False, None, str(exc)
        if completed.returncode:
            return False, None, "unreachable"
        output = completed.stdout + "\n" + completed.stderr
        match = re.search(r"time\s*[=<]\s*(\d+(?:\.\d+)?)\s*ms", output, re.I)
        if not match:
            return True, None, "latency unavailable"
        return True, 0.5 if re.search(r"time\s*<\s*1\s*ms", output, re.I) else float(match.group(1)), ""


class NetworkPlugin(OrionPlugin):
    name, version = "network", "1.0.0"
    description = "Monitor the local gateway and Internet stability."

    def __init__(self):
        self.monitor: NetworkMonitor | None = None

    def activate(self, context: PluginContext) -> None:
        paths = getattr(context.orion, "paths", None)
        root = getattr(paths, "user_root", Path.home() / ".orion")
        self.monitor = NetworkMonitor(Path(root) / "logs" / "network")
        context.services.register("network", self.monitor)
        context.orion.network_monitor = self.monitor

    def deactivate(self) -> None:
        if self.monitor and self.monitor.running:
            self.monitor.stop()

    def handle(self, command: str) -> bool:
        raw, lower = command.strip(), command.strip().lower()
        if lower == "network":
            print("Usage: network <status|watch [seconds]|stop|report|config>")
        elif lower == "network status": self._status()
        elif lower == "network watch": self._watch(None)
        elif lower.startswith("network watch "): self._watch(raw[14:].strip())
        elif lower == "network stop": self._stop()
        elif lower == "network report": self._report()
        elif lower == "network config": self._config()
        else: return False
        return True

    def help_lines(self) -> list[str]:
        return ["  network status         Check router and Internet now [plugin]",
                "  network watch [sec]    Start background outage monitoring [plugin]",
                "  network stop           Stop monitoring and save summary [plugin]",
                "  network report         Show the current monitoring report [plugin]",
                "  network config         Show monitored targets and settings [plugin]"]

    def _required(self) -> NetworkMonitor:
        if self.monitor is None: raise RuntimeError("Network plugin is not active.")
        return self.monitor

    def _status(self) -> None:
        results = self._required().check_once()
        print("\nNetwork Status\n" + "-" * 58)
        for result in results:
            state = ("unknown" if result.latency_ms is None else f"{result.latency_ms:.1f} ms") if result.online else "offline"
            print(f"[{'OK' if result.online else '--'}] {result.name:<12} {result.host:<15} {state}")

    def _watch(self, value: str | None) -> None:
        try: interval = float(value) if value else None
        except ValueError:
            print("Network Error: interval must be a number of seconds."); return
        monitor = self._required()
        if not monitor.start(interval): print("Network monitoring is already running."); return
        print(f"Network monitoring started. Log: {monitor.log_path}")

    def _stop(self) -> None:
        monitor = self._required()
        if not monitor.stop(): print("Network monitoring is not running."); return
        print("Network monitoring stopped."); self._print_summary(monitor.summary())

    def _report(self) -> None: self._print_summary(self._required().summary())

    def _config(self) -> None:
        monitor = self._required(); print("\nNetwork Monitor Configuration\n" + "-" * 58)
        for name, host in monitor.targets.items(): print(f"{name:<12}: {host}")
        print(f"Interval    : {monitor.interval_seconds:.0f} seconds\nTimeout     : {monitor.timeout_ms} ms\nLog folder  : {monitor.log_directory}")

    @staticmethod
    def _print_summary(summary: dict[str, object]) -> None:
        print("\nNetwork Report\n" + "-" * 58)
        print(f"Running   : {'Yes' if summary['running'] else 'No'}\nStarted   : {summary['started_at'] or 'Not started'}")
        for name, values in summary["targets"].items():
            state = "online" if values["online"] is True else "offline" if values["online"] is False else "unknown"
            print(f"{name:<12} {state:<7} | outages {values['outages']} | loss {values['packet_loss_percent']:.2f}% | avg {values['average_latency_ms']:.1f} ms")
        print(f"Diagnosis : {summary['diagnosis']}")


def create_plugin() -> OrionPlugin:
    return NetworkPlugin()
