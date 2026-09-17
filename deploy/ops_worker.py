"""Host-side operations worker for the container deployment.

The worker intentionally has no network listener.  Backend and the worker exchange
small JSON files through ``deploy/operations``; Docker access stays on the host.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import hashlib
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from technical_config import DEFAULTS, ENV_KEYS, LIMITS, validate as validate_configuration
OPERATIONS_DIR = ROOT / "deploy" / "operations"
BACKUP_DIR = ROOT / "deploy" / "backups"
ENV_FILE = ROOT / ".env.docker"
COMPOSE_FILE = ROOT / "docker-compose.yml"
POLL_SECONDS = 15
REQUEST_MAX_AGE_SECONDS = 120
RESULT_LIMIT = 200
CONTROLLED_SERVICES = frozenset({"voice", "asterisk"})
ALL_SERVICES = ("postgres", "backend", "frontend", "asterisk", "voice")
SAFE_ENV_KEYS = (
    "WEB_BIND_ADDRESS",
    "SIP_BIND_ADDRESS",
    "SIP_EXTERNAL_ADDRESS",
    "SIP_LOCAL_NET",
    "ALLOWED_EXTENSIONS",
    "PIPELINE_MODE",
    "TOPOLOGY_VERIFIED",
)


def _epoch() -> int:
    return int(time.time())


def _atomic_json(path: Path, value: Any) -> None:
    """Replace a JSON file atomically without ever using a caller-provided path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


class SingleInstanceLock:
    """PID lock with safe recovery when the recorded process no longer exists."""

    def __init__(self, path: Path):
        self.path = path
        self.acquired = False

    @staticmethod
    def _alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            process_query_limited_information = 0x1000
            still_active = 259
            try:
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
                kernel32.OpenProcess.restype = wintypes.HANDLE
                kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
                kernel32.GetExitCodeProcess.restype = wintypes.BOOL
                kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
                kernel32.CloseHandle.restype = wintypes.BOOL
                handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
                if not handle:
                    # Access denied means a process exists but cannot be queried.
                    return ctypes.get_last_error() == 5
                exit_code = wintypes.DWORD()
                try:
                    return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
                finally:
                    kernel32.CloseHandle(handle)
            except (AttributeError, OSError):
                # Never risk taking over a lock when the platform query itself fails.
                return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                data = _read_json(self.path, {})
                pid = data.get("pid") if isinstance(data, dict) else None
                if isinstance(pid, int) and self._alive(pid):
                    return False
                if not isinstance(pid, int):
                    try:
                        if time.time() - self.path.stat().st_mtime < 30:
                            return False
                    except OSError:
                        return False
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    return False
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "started_at": _epoch()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            self.acquired = True
            return True
        return False

    def release(self) -> None:
        if not self.acquired:
            return
        # Only remove our own lock.  A replaced lock must be left untouched.
        data = _read_json(self.path, {})
        if isinstance(data, dict) and data.get("pid") == os.getpid():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            raise RuntimeError("operations worker is already running")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class OperationsWorker:
    def __init__(
        self,
        *,
        root: Path = ROOT,
        mock: bool = False,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        now: Callable[[], float] = time.time,
    ):
        self.root = root.resolve()
        operations_root = self.root / "deploy" / "operations"
        # Mock jobs have their own queue and durable state.  A harmless mock run
        # therefore cannot consume a live command or suppress a real daily backup.
        self.operations_dir = operations_root / "mock" if mock else operations_root
        self.backup_dir = self.root / "deploy" / "backups"
        self.env_file = self.root / ".env.docker"
        self.compose_file = self.root / "docker-compose.yml"
        self.mock = mock
        self.runner = runner
        self.now = now
        self.operations_dir.mkdir(parents=True, exist_ok=True)
        (self.operations_dir / "requests").mkdir(exist_ok=True)
        (self.operations_dir / "processed").mkdir(exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.operations_dir / "worker_state.json"
        self.completed_path = self.operations_dir / "completed.json"
        self.status_path = self.operations_dir / "status.json"
        self.events_path = self.operations_dir / "events.jsonl"
        state = _read_json(self.state_path, {})
        self.state: dict[str, Any] = state if isinstance(state, dict) else {}
        completed = _read_json(self.completed_path, [])
        self.completed: list[dict[str, Any]] = completed if isinstance(completed, list) else []
        recovered = False
        for item in self.completed:
            if isinstance(item, dict) and item.get("status") == "in_progress":
                item["status"] = "failed"
                item["error"] = "worker interrupted"
                item["at"] = int(self.now())
                self._persist_result(item)
                recovered = True
        if recovered:
            _atomic_json(self.completed_path, self.completed[-RESULT_LIMIT:])

    def _compose(self, args: list[str], *, timeout: int = 45, stdout: Any = subprocess.PIPE) -> subprocess.CompletedProcess:
        command = [
            "docker", "compose", "--env-file", str(self.env_file),
            "-f", str(self.compose_file),
        ]
        profile = _read_json(self.operations_dir / 'deployment-profile.json', {})
        if profile.get('directory'):
            command += ['--env-file',str(self.root/'deploy/directory/private/test.env'),'-f',str(self.root/'deploy/directory/compose.yaml')]
        if profile.get('cluster'):
            command += ['-f',str(self.root/'deploy/cluster/compose.yaml')]
        if profile.get('tls'):
            command += ['-f',str(self.root/'deploy/tls/docker-compose.tls.yml'),'--profile','tls']
            if profile.get('cluster'):command += ['-f',str(self.root/'deploy/cluster/compose.tls.yaml')]
        command += args
        options: dict[str, Any] = {
            "cwd": self.root, "stdin": subprocess.DEVNULL, "stdout": stdout,
            "stderr": subprocess.PIPE, "shell": False, "timeout": timeout,
            "text": stdout == subprocess.PIPE,
        }
        if os.name == "nt":
            options["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        return self.runner(command, **options)

    def _maintenance_up(self, services):
        profile=_read_json(self.operations_dir/'deployment-profile.json',{})
        args=['up','-d','--no-build','--wait','--wait-timeout','180']
        if profile.get('cluster'):args += ['--scale','backend=1']
        result=self._compose(args+services,timeout=240)
        if result.returncode==0 and profile.get('cluster'):
            result=self._compose(['up','-d','--no-deps','--no-build','--wait','--wait-timeout','180',
                                  '--scale','backend=2','backend'],timeout=240)
        return result

    def _event(self, kind: str, message: str, *, service: str | None = None) -> None:
        event: dict[str, Any] = {"at": int(self.now()), "type": kind, "message": message}
        if service:
            event["service"] = service
        # Event messages are fixed literals assembled by this module: never append
        # subprocess output, environment values, or exception representations.
        with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _load_settings(self) -> dict[str, Any]:
        raw = _read_json(self.operations_dir / "settings.json", {})
        if not isinstance(raw, dict):
            self._event("error", "invalid settings; defaults applied")
            raw = {}
        enabled = raw.get("backup_enabled", True)
        hour = raw.get("backup_hour_utc", 0)
        if not isinstance(enabled, bool) or not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
            self._event("error", "invalid settings; defaults applied")
            return {"backup_enabled": True, "backup_hour_utc": 0}
        return {"backup_enabled": enabled, "backup_hour_utc": hour}

    def _safe_configuration(self) -> dict[str, Any]:
        profile=_read_json(self.operations_dir/'deployment-profile.json',{})
        values: dict[str, str] = {}
        try:
            lines = self.env_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        allowed = set(SAFE_ENV_KEYS)
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            if key in allowed:
                values[key] = value.strip().strip('"').strip("'")
        return {
            "mode": "mock" if self.mock else "docker-compose",
            "web_bind_address": values.get("WEB_BIND_ADDRESS", "127.0.0.1"),
            "web_port": 3000,
            "sip_bind_address": values.get("SIP_BIND_ADDRESS", "127.0.0.1"),
            "sip_external_address": values.get("SIP_EXTERNAL_ADDRESS", "127.0.0.1"),
            "sip_local_net": values.get("SIP_LOCAL_NET", "172.16.0.0/12"),
            "sip_port": 5061 if profile.get('tls') else 5060,
            "encrypted_transport": bool(profile.get('tls')),
            "backend_cluster": bool(profile.get('cluster')),
            "rtp_ports": "20000-20199/udp",
            "allowed_extensions": values.get("ALLOWED_EXTENSIONS", "201"),
            "pipeline_mode": values.get("PIPELINE_MODE", "spike"),
            "topology_verified": values.get("TOPOLOGY_VERIFIED", "false").lower() == "true",
            "database": {"engine": "postgresql", "major_version": 16, "name": "trainer"},
        }

    def _service_names(self) -> tuple[str, ...]:
        profile = _read_json(self.operations_dir / 'deployment-profile.json', {})
        return ALL_SERVICES + (('directory',) if profile.get('directory') else ()) + (('backend-lb',) if profile.get('cluster') else ())

    def _services(self) -> list[dict[str, Any]]:
        names = self._service_names()
        if self.mock:
            return [{"name": name, "state": "mock", "health": "mock"} for name in names]
        if not self.env_file.is_file():
            return [{"name": name, "state": "unknown", "health": "unknown"} for name in names]
        try:
            result = self._compose(["ps", "--format", "json"], timeout=30)
        except (OSError, subprocess.SubprocessError):
            return [{"name": name, "state": "unknown", "health": "unknown"} for name in names]
        if result.returncode != 0:
            return [{"name": name, "state": "unknown", "health": "unknown"} for name in names]
        text = result.stdout or ""
        try:
            parsed = json.loads(text)
            rows = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            try:
                rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            except json.JSONDecodeError:
                rows = []
        by_name: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("Service") or row.get("service") or "")
            if name not in names:
                continue
            state = str(row.get("State") or row.get("state") or "unknown").lower()
            health = str(row.get("Health") or row.get("health") or "none").lower()
            by_name.setdefault(name, []).append({"state": state, "health": health})
        services = []
        for name in names:
            replicas = by_name.get(name, [])
            expected = 2 if name == 'backend' and 'backend-lb' in names else 1
            running = sum(replica['state'] == 'running' for replica in replicas)
            healthy = sum(replica['state'] == 'running' and replica['health'] == 'healthy' for replica in replicas)
            services.append({"name": name,
                "state": "running" if running >= expected else "degraded" if running else "stopped",
                "health": "healthy" if healthy >= expected and healthy == len(replicas) else "unhealthy" if replicas else "none",
                "replicas": len(replicas), "healthy_replicas": healthy, "expected_replicas": expected})
        return services

    @staticmethod
    def _windows_cpu_times() -> tuple[int, int] | None:
        """Return idle and total 100ns ticks using the Windows kernel API."""
        class FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

        idle, kernel, user = FileTime(), FileTime(), FileTime()
        try:
            if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            ):
                return None
        except (AttributeError, OSError):
            return None

        def ticks(value: FileTime) -> int:
            return (int(value.high) << 32) | int(value.low)

        # Kernel time includes idle time.
        return ticks(idle), ticks(kernel) + ticks(user)

    def _metrics(self) -> dict[str, Any]:
        disk = shutil.disk_usage(self.root)
        cpu_percent: float | None = None
        memory_total: int | None = None
        memory_available: int | None = None
        source = "stdlib"
        cpu_source = "unavailable"
        try:
            import psutil  # type: ignore

            cpu_percent = round(float(psutil.cpu_percent(interval=None)), 1)
            cpu_source = "psutil_utilization"
            memory = psutil.virtual_memory()
            memory_total = int(memory.total)
            memory_available = int(memory.available)
            source = "psutil"
        except (ImportError, AttributeError, OSError):
            if hasattr(os, "getloadavg"):
                try:
                    cpu_percent = round(min(100.0, os.getloadavg()[0] * 100 / (os.cpu_count() or 1)), 1)
                    cpu_source = "load_average_estimate"
                except OSError:
                    pass
            if os.name == "nt":
                cpu_times = self._windows_cpu_times()
                previous_times = self.state.get("windows_cpu_times")
                if cpu_times:
                    self.state["windows_cpu_times"] = list(cpu_times)
                    if isinstance(previous_times, list) and len(previous_times) == 2:
                        idle_delta = cpu_times[0] - int(previous_times[0])
                        total_delta = cpu_times[1] - int(previous_times[1])
                        if total_delta > 0:
                            cpu_percent = round(max(0.0, min(100.0, (total_delta - idle_delta) * 100 / total_delta)), 1)
                            cpu_source = "windows_system_times"
                class MemoryStatus(ctypes.Structure):
                    _fields_ = [
                        ("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                        ("total_page", ctypes.c_ulonglong), ("available_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                        ("available_extended", ctypes.c_ulonglong),
                    ]
                memory = MemoryStatus()
                memory.length = ctypes.sizeof(MemoryStatus)
                try:
                    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
                        memory_total, memory_available = int(memory.total), int(memory.available)
                except (AttributeError, OSError):
                    pass
            else:
                try:
                    page = int(os.sysconf("SC_PAGE_SIZE"))
                    memory_total = page * int(os.sysconf("SC_PHYS_PAGES"))
                    memory_available = page * int(os.sysconf("SC_AVPHYS_PAGES"))
                except (AttributeError, OSError, ValueError):
                    pass
        memory_used_percent = None
        if memory_total and memory_available is not None:
            memory_used_percent = round((memory_total - memory_available) * 100 / memory_total, 1)
        return {
            "source": source,
            "cpu_percent": cpu_percent,
            "cpu_source": cpu_source,
            "memory": {"total_bytes": memory_total, "available_bytes": memory_available, "used_percent": memory_used_percent},
            "disk": {
                "total_bytes": disk.total,
                "free_bytes": disk.free,
                "used_percent": round(disk.used * 100 / disk.total, 1) if disk.total else None,
            },
        }

    def _metric_events(self, metrics: dict[str, Any]) -> None:
        values = {
            "cpu": metrics.get("cpu_percent"),
            "memory": metrics.get("memory", {}).get("used_percent"),
            "disk": metrics.get("disk", {}).get("used_percent"),
        }
        previous = self.state.get("metric_conditions", {})
        if not isinstance(previous, dict):
            previous = {}
        current: dict[str, str] = {}
        for name, value in values.items():
            condition = "unavailable" if value is None else "warning" if value > 90 else "normal"
            current[name] = condition
            old = previous.get(name)
            if old == condition:
                continue
            if condition == "warning":
                self._event("warning", f"host {name} usage above 90 percent")
            elif condition == "unavailable":
                self._event("error", f"host {name} metric unavailable")
            elif old in {"warning", "unavailable"}:
                self._event("recovery", f"host {name} metric recovered")
        self.state["metric_conditions"] = current

    def _backup_manifest(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.backup_dir.iterdir(), reverse=True):
            if not path.is_file() or path.suffix not in {".dump", ".t112", ".failed", ".partial"}:
                continue
            stat = path.stat()
            status = "complete" if path.suffix in (".dump", ".t112") else "failed" if path.suffix == ".failed" else "in_progress"
            result.append({"name": path.name, "size_bytes": stat.st_size, "created_at": int(stat.st_mtime), "status": status})
        return result[:RESULT_LIMIT]

    def _persist_result(self, result: dict[str, Any]) -> None:
        identifier = result.get("id")
        if isinstance(identifier, str):
            _atomic_json(self.operations_dir / "processed" / f"{identifier}.result.json", result)

    def _archive_request(self, path: Path, identifier: str) -> None:
        destination = self.operations_dir / "processed" / f"{identifier}.request.json"
        if destination.exists():
            return
        try:
            os.replace(path, destination)
        except FileNotFoundError:
            pass
        except OSError:
            self._event("error", "request archival failed")

    def _backup(self) -> tuple[str, str | None]:
        if self.mock:
            return "simulated", None
        mode = _read_json(self.operations_dir / 'backup-executor.json', {})
        if isinstance(mode, dict) and mode.get('mode') == 'container':
            return "delegated", None
        stamp = datetime.fromtimestamp(self.now(), timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        final = self.backup_dir / f"postgres-{stamp}.dump"
        partial = self.backup_dir / f"postgres-{stamp}.partial"
        failed = self.backup_dir / f"postgres-{stamp}.failed"
        if not self.env_file.is_file():
            return "failed", "backup failed"
        try:
            with partial.open("xb") as output:
                result = self._compose(
                    ["exec", "-T", "postgres", "pg_dump", "-U", "trainer", "-d", "trainer", "-Fc"],
                    timeout=300,
                    stdout=output,
                )
            if result.returncode == 0:
                os.replace(partial, final)
                from full_backup import create
                try:
                    create(self,final,stamp)
                except (OSError,ValueError,RuntimeError,ImportError,subprocess.SubprocessError):
                    return 'failed','database saved; full encrypted backup failed'
                return "completed", None
            os.replace(partial, failed)
        except (OSError, subprocess.SubprocessError):
            if partial.exists():
                try:
                    os.replace(partial, failed)
                except OSError:
                    pass
        return "failed", "backup failed"

    def _perform_request(self, request: dict[str, Any]) -> dict[str, Any]:
        identifier = request["id"]
        action = request["action"]
        service = request.get("service")
        at = int(self.now())
        if action == "backup":
            status, error = self._backup()
        elif self.mock:
            status, error = "simulated", None
        elif action == 'configure':
            status,error=self._configure(request['configuration'])
        elif action == 'update':
            status,error=self._update()
        else:
            try:
                result = self._compose([action, service], timeout=90)
                status, error = ("completed", None) if result.returncode == 0 else ("failed", "operation failed")
            except (OSError, subprocess.SubprocessError):
                status, error = "failed", "operation failed"
        completed = {"id": identifier, "action": action, "service": service, "status": status, "at": at, "error": error}
        event_kind = "operation" if error is None else "error"
        self._event(event_kind, f"{action} {status}", service=service)
        return completed

    def _validate_request(self, raw: Any) -> tuple[dict[str, Any] | None, str | None]:
        if not isinstance(raw, dict):
            return None, "invalid request"
        identifier = raw.get("id")
        action = raw.get("action")
        service = raw.get("service")
        created_at = raw.get("created_at")
        try:
            canonical_id = str(UUID(identifier)) if isinstance(identifier, str) else ""
        except ValueError:
            canonical_id = ""
        if not canonical_id or canonical_id != identifier:
            return None, "invalid request"
        request = {"id": canonical_id, "action": action, "service": service}
        if action not in {"backup", "start", "stop", "restart", "configure", "update"}:
            return request, "invalid request"
        if action in ("backup", "configure", "update"):
            if service not in (None, ""):
                return request, "invalid request"
            service = None
        elif service not in CONTROLLED_SERVICES:
            return request, "service is not allowed"
        request["service"] = service
        if action=='configure':
            try:
                request['configuration']=validate_configuration(raw.get('configuration'))
                if not request['configuration']:return request,'invalid configuration'
            except (ValueError,TypeError):return request,'invalid configuration'
        if not isinstance(created_at, (int, float)) or isinstance(created_at, bool):
            return request, "invalid request"
        age = self.now() - float(created_at)
        if age > REQUEST_MAX_AGE_SECONDS or age < -60:
            return request, "request expired"
        return request, None

    def _configure(self, values):
        """ENV-only allowlist. No shell, credential editor or arbitrary Compose input."""
        try:
            values=validate_configuration(values)
            original=self.env_file.read_text(encoding='utf-8')
            keys={ENV_KEYS[key]:str(value) for key,value in values.items()}
            lines=[]
            for line in original.splitlines():
                key=line.split('=',1)[0].strip()
                if key not in keys:lines.append(line)
            lines += [key+'='+value for key,value in keys.items()]
            fd,name=tempfile.mkstemp(dir=self.env_file.parent,prefix='.runtime-',suffix='.tmp')
            with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:
                stream.write('\n'.join(lines)+'\n');stream.flush();os.fsync(stream.fileno())
            os.replace(name,self.env_file)
            result=self._maintenance_up(['postgres','backend','voice','asterisk'])
            if result.returncode:
                # Preserve applied ENV for diagnosis; never silently report rollback.
                return 'failed','configuration saved; service restart failed'
            self._publish_configuration()
            return 'completed',None
        except (OSError,ValueError,subprocess.SubprocessError):
            return 'failed','configuration apply failed'

    def _publish_configuration(self):
        values=dict(DEFAULTS)
        try:
            raw=dict(line.split('=',1) for line in self.env_file.read_text(encoding='utf-8').splitlines()
                     if '=' in line and not line.lstrip().startswith('#'))
            for key,env in ENV_KEYS.items():
                if env in raw:
                    clean=raw[env].strip().strip('"').strip("'")
                    values[key]=int(clean) if key in LIMITS else clean
            validate_configuration(values)
            _atomic_json(self.operations_dir/'runtime-configuration.json',values)
        except (ValueError,OSError,TypeError):
            self._event('error','runtime configuration unavailable')

    def _update(self):
        """Only host-approved, content-addressed offline packages; no browser upload."""
        folder=self.root/'deploy/updates'
        manifest=_read_json(folder/'approved.json',{})
        digest=manifest.get('sha256','')
        if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
            return 'rejected','no approved update'
        archive=folder/(digest+'.tar')
        try:
            with archive.open('rb') as source:
                actual=hashlib.file_digest(source,'sha256').hexdigest()
            if actual!=digest:return 'rejected','update checksum mismatch'
            backup_status,backup_error=self._backup()
            if backup_error:return 'failed','pre-update backup failed'
            result=self.runner(['docker','image','load','--input',str(archive)],cwd=self.root,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                timeout=600,shell=False,**({'creationflags':0x08000000} if os.name=='nt' else {}))
            if result.returncode:return 'failed','image import failed'
            result=self._maintenance_up([])
            if result.returncode:return 'failed','update loaded; service startup failed'
            _atomic_json(folder/'installed.json',{'sha256':digest,'at':int(self.now())})
            return 'completed',None
        except (OSError,subprocess.SubprocessError):return 'failed','update failed'

    def _publish_logs(self):
        """Bounded container log tail, scrubbed using known ENV secrets and patterns."""
        try:
            if self.mock:
                lines=['Mock: service logs are simulated.']
            else:
                result=self._compose(['logs','--no-color','--timestamps','--tail','30',*self._service_names()],timeout=30)
                if result.returncode:raise OSError('Logs unavailable')
                value=(result.stdout or '')[-131072:]
                # Sensitive values stay local. Never publish the values or exceptions.
                for envfile in [self.env_file,self.root/'deploy/directory/private/test.env']:
                    if not envfile.exists():continue
                    for line in envfile.read_text(encoding='utf-8').splitlines():
                        if '=' not in line or line.lstrip().startswith('#'):continue
                        key,secret=line.split('=',1);secret=secret.strip().strip("'").strip('"')
                        if re.search('PASSWORD|TOKEN|KEY|SECRET|ACCOUNTS',key,re.I):
                            if len(secret)>3:value=value.replace(secret,'[REDACTED]')
                            if 'ACCOUNTS' in key:
                                try:
                                    for item in json.loads(secret).values():value=value.replace(str(item),'[REDACTED]')
                                except (ValueError,AttributeError):pass
                value=re.sub(r'(?i)(authorization|password|secret|token|api[_-]?key)([\s=:]+)[^\s,;]+',r'\1\2[REDACTED]',value)
                value=re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[REDACTED]@',value)
                value=re.sub(r'sk-or-v1-[A-Za-z0-9_-]+','[REDACTED]',value)
                value=re.sub(r'(?i)Bearer\s+[^\s,;]+','Bearer [REDACTED]',value)
                lines=value.splitlines()[-200:]
            _atomic_json(self.operations_dir/'service-logs.json',{'sampled_at':int(self.now()),'available':True,'lines':lines})
        except (OSError,subprocess.SubprocessError):
            _atomic_json(self.operations_dir/'service-logs.json',{'sampled_at':int(self.now()),'available':False,'lines':[]})

    def _consume_requests(self) -> None:
        known = {item.get("id") for item in self.completed if isinstance(item, dict)}
        invalid_seen = set(self.state.get("invalid_requests", []))
        changed = False
        for path in sorted((self.operations_dir / "requests").glob("*.json")):
            if not path.is_file():
                continue
            signature = f"{path.name}:{path.stat().st_mtime_ns}:{path.stat().st_size}"
            raw = _read_json(path, None)
            request, error = self._validate_request(raw)
            mode = _read_json(self.operations_dir / 'backup-executor.json', {})
            if (request and request.get('action') == 'backup' and isinstance(mode, dict)
                    and mode.get('mode') == 'container'):
                continue
            persisted_result = (
                self.operations_dir / "processed" / f"{request['id']}.result.json"
                if request else None
            )
            if request and persisted_result and persisted_result.is_file():
                self._archive_request(path, request["id"])
                continue
            if request and request["id"] in known:
                self._archive_request(path, request["id"])
                continue
            if request and error:
                result = {**request, "status": "rejected", "at": int(self.now()), "error": error}
                self.completed.append(result)
                known.add(request["id"])
                self._persist_result(result)
                self._archive_request(path, request["id"])
                self._event("error", error, service=request.get("service"))
                changed = True
            elif request:
                in_progress = {**request, "status": "in_progress", "at": int(self.now()), "error": None}
                self.completed.append(in_progress)
                self.completed = self.completed[-RESULT_LIMIT:]
                _atomic_json(self.completed_path, self.completed)
                result = self._perform_request(request)
                self.completed[-1] = result
                known.add(request["id"])
                self._persist_result(result)
                self._archive_request(path, request["id"])
                changed = True
            elif signature not in invalid_seen:
                invalid_seen.add(signature)
                self._event("error", error or "invalid request")
        self.state["invalid_requests"] = sorted(invalid_seen)
        if changed or not self.completed_path.exists():
            self.completed = self.completed[-RESULT_LIMIT:]
            _atomic_json(self.completed_path, self.completed)

    def _scheduled_backup(self, settings: dict[str, Any]) -> None:
        mode = _read_json(self.operations_dir / 'backup-executor.json', {})
        if isinstance(mode, dict) and mode.get('mode') == 'container':
            return
        if not settings["backup_enabled"]:
            return
        now = datetime.fromtimestamp(self.now(), timezone.utc)
        today = now.date().isoformat()
        previous = self.state.get("last_scheduled_backup")
        recent_failure = (
            isinstance(previous, dict)
            and previous.get("status") == "failed"
            and self.now() - float(previous.get("at", 0)) < 900
        )
        if now.hour < settings["backup_hour_utc"] or self.state.get("last_scheduled_backup_date") == today or recent_failure:
            return
        status, error = self._backup()
        self.state["last_scheduled_backup"] = {"at": int(self.now()), "status": status, "error": error}
        if error is None:
            self.state["last_scheduled_backup_date"] = today
        self._event("backup" if error is None else "error", f"scheduled backup {status}")

    def run_once(self) -> dict[str, Any]:
        self._publish_configuration()
        self._publish_logs()
        settings = self._load_settings()
        self._consume_requests()
        self._scheduled_backup(settings)
        services = self._services()
        previous = self.state.get("service_states", {})
        current = {item["name"]: {"state": item["state"], "health": item["health"]} for item in services}
        sampling_unavailable = all(item["state"] == "unknown" for item in services)
        was_unavailable = self.state.get("service_sampling_unavailable")
        if sampling_unavailable and was_unavailable is not True:
            self._event("error", "service sampling unavailable")
        elif not sampling_unavailable and was_unavailable is True:
            self._event("recovery", "service sampling recovered")
        self.state["service_sampling_unavailable"] = sampling_unavailable
        previous_conditions = self.state.get("service_conditions", {})
        if not isinstance(previous_conditions, dict):
            previous_conditions = {}
        current_conditions: dict[str, str] = {}
        for item in services:
            name = item["name"]
            okay = self.mock or (item["state"] == "running" and item["health"] not in {"unhealthy", "starting"})
            condition = "healthy" if okay else "unavailable" if item["state"] == "unknown" else "unhealthy"
            current_conditions[name] = condition
            old = previous_conditions.get(name)
            if condition == old:
                continue
            if condition in {"unhealthy", "unavailable"}:
                self._event("error", "service is not healthy", service=name)
            elif old in {"unhealthy", "unavailable"}:
                self._event("recovery", "service recovered", service=name)
        self.state["service_conditions"] = current_conditions
        self.state["service_states"] = current
        metrics = self._metrics()
        self._metric_events(metrics)
        self.state["updated_at"] = int(self.now())
        _atomic_json(self.state_path, self.state)
        status = {
            "sampled_at": int(self.now()),
            "mock": self.mock,
            "services": services,
            "metrics": metrics,
            "backups": self._backup_manifest(),
            "last_scheduled_backup": self.state.get("last_scheduled_backup"),
            "settings": settings,
            "configuration": self._safe_configuration(),
        }
        _atomic_json(self.status_path, status)
        return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mock", action="store_true", help="publish mock state and simulate every operation")
    parser.add_argument("--once", action="store_true", help="perform one poll and exit")
    args = parser.parse_args(argv)
    worker = OperationsWorker(mock=args.mock)
    lock = SingleInstanceLock(worker.operations_dir / "worker.lock")
    if not lock.acquire():
        print("Operations worker is already running.", file=sys.stderr)
        return 2
    try:
        while True:
            worker.run_once()
            if args.once:
                return 0
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
