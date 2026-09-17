"""Bounded synthetic acceptance load for the local Backend/Frontend workspace.

Starts isolated loopback services with a temporary SQLite database and mock LLM.
It never calls Voice, an external model, or an emergency-service integration.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import json
import os
import platform
import secrets
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx


ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 2)


def summary(values: list[float]) -> dict:
    return {
        "count": len(values),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "max_ms": round(max(values), 2) if values else None,
    }


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def physical_memory_bytes() -> int | None:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                        ("page_total", ctypes.c_ulonglong), ("page_available", ctypes.c_ulonglong),
                        ("virtual_total", ctypes.c_ulonglong), ("virtual_available", ctypes.c_ulonglong),
                        ("extended_available", ctypes.c_ulonglong)]
        value = MemoryStatus()
        value.length = ctypes.sizeof(value)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            return int(value.total)
    return None


def process_rss_bytes(pid: int) -> int | None:
    if os.name != "nt":
        try:
            pages = int(Path(f"/proc/{pid}/statm").read_text().split()[1])
            return pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            return None
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    access = 0x0400 | 0x0010
    handle = ctypes.windll.kernel32.OpenProcess(access, False, pid)
    if not handle:
        return None
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    return None


async def wait_ready(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(url)
                if response.status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise RuntimeError(f"service did not become ready: {url}")


def checked(response: httpx.Response, expected: tuple[int, ...] = (200, 201)) -> dict:
    if response.status_code not in expected:
        detail = response.text[:300].replace("\n", " ")
        raise RuntimeError(f"{response.request.method} {response.request.url}: {response.status_code} {detail}")
    return response.json() if response.content else {}


async def run(args: argparse.Namespace) -> dict:
    started_at = time.time()
    backend_port, frontend_port = free_port(), free_port()
    temp_dir = Path(tempfile.mkdtemp(prefix="trainer112-load-"))
    db_path = temp_dir / "synthetic.sqlite3"
    token = secrets.token_urlsafe(32)
    password = "Synthetic-load-password-2026"
    env = os.environ.copy()
    audit_dir = temp_dir / "security-audit"
    env.update({
        "LLM_PROVIDER": "mock",
        "DIALOGUE_DB": str(db_path),
        "DIALOGUE_TOKEN": token,
        "BACKEND_TOKEN": token,
        "DIALOGUE_URL": f"http://127.0.0.1:{backend_port}",
        "PYTHONUNBUFFERED": "1",
    })
    # The Compose deployment always runs with PostgreSQL and an enabled security
    # audit; measuring only temporary SQLite without the audit would report a
    # throughput the delivered stand never reaches.
    if args.database_url:
        env["DATABASE_URL"] = args.database_url
        env.pop("DIALOGUE_DB", None)
    if args.security_audit:
        audit_dir.mkdir(parents=True, exist_ok=True)
        env["SECURITY_AUDIT_DIR"] = str(audit_dir)
    else:
        env.pop("SECURITY_AUDIT_DIR", None)

    def fixture_connection():
        """Same portable facade Backend uses, so one fixture serves both engines."""
        sys.path.insert(0, str(ROOT / "backend"))
        from database import connect_database
        return connect_database(args.database_url or str(db_path))
    # Prevent inherited provider credentials/configuration from changing the fixture.
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OLLAMA_MODEL", "VOICE_API_TOKEN"):
        env.pop(key, None)
    logs = {}
    processes: list[subprocess.Popen] = []
    samplers: list[asyncio.Task] = []
    rss_samples: dict[str, list[int]] = {"backend": [], "frontend": []}
    stop_sampling = asyncio.Event()

    async def sample_processes() -> None:
        while not stop_sampling.is_set():
            for name, process in zip(("backend", "frontend"), processes):
                value = process_rss_bytes(process.pid)
                if value is not None:
                    rss_samples[name].append(value)
            try:
                await asyncio.wait_for(stop_sampling.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass

    try:
        for name, port, cwd in (("backend", backend_port, ROOT / "backend"),
                                ("frontend", frontend_port, ROOT / "frontend")):
            log_path = temp_dir / f"{name}.log"
            logs[name] = log_path
            stream = log_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port),
                 "--log-level", "warning", "--no-access-log"],
                cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
            )
            process._acceptance_stream = stream  # type: ignore[attr-defined]
            processes.append(process)
        await wait_ready(f"http://127.0.0.1:{backend_port}/api/v1/health")
        await wait_ready(f"http://127.0.0.1:{frontend_port}/api/v1/health")
        samplers.append(asyncio.create_task(sample_processes()))

        service_headers = {"Authorization": "Bearer " + token}
        setup_begin = time.perf_counter()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{backend_port}", headers=service_headers,
                                     timeout=60, trust_env=False) as backend:
            root = checked(await backend.post("/api/v1/auth/bootstrap", json={
                "username": "loadadmin", "password": password, "display_name": "Synthetic administrator"}))
            admin = {"X-User-Session": root["session_token"]}
            checked(await backend.post("/api/v1/admin/users", headers=admin, json={
                "username": "loadteacher", "password": password,
                "display_name": "Synthetic teacher", "role": "teacher"}))
            teacher_login = checked(await backend.post("/api/v1/auth/login", json={
                "username": "loadteacher", "password": password}))
            teacher = {"X-User-Session": teacher_login["session_token"]}
            # Fixture setup is intentionally outside the measured phases. Reuse the
            # valid synthetic password hash and pre-issue sessions so 100 serial
            # scrypt calls do not turn a load test into an account-provisioning test.
            students, student_tokens = [], []
            fixture_db = fixture_connection()
            with fixture_db:
                salt, password_hash = fixture_db.execute(
                    "SELECT password_salt,password_hash FROM account_users WHERE username='loadteacher'"
                ).fetchone()
                created = int(time.time())
                for index in range(args.read_users):
                    uid, session_token = str(uuid4()), secrets.token_urlsafe(32)
                    students.append({"id": uid, "username": f"loadstudent{index:03d}",
                                     "display_name": f"Synthetic student {index:03d}",
                                     "role": "student", "active": True})
                    student_tokens.append(session_token)
                    fixture_db.execute("INSERT INTO account_users VALUES (?,?,?,?,?,?,?,?)", (
                        uid, students[-1]["username"], students[-1]["display_name"], "student", 1,
                        salt, password_hash, created))
                    fixture_db.execute("INSERT INTO account_sessions VALUES (?,?,?,?)", (
                        hashlib.sha256(session_token.encode("ascii")).hexdigest(), uid,
                        created + 8 * 60 * 60, created))
            group = checked(await backend.post("/api/v1/instructor/groups", headers=teacher,
                                               json={"title": "Synthetic acceptance group"}))
            with fixture_db:
                for student in students:
                    fixture_db.execute("INSERT INTO group_members VALUES (?,?)",
                                       (group["id"], student["id"]))
            fixture_db.close()
            scenarios = checked(await backend.get("/api/v1/scenarios", headers=teacher), (200,))
            scenario_id = next(item["id"] for item in scenarios if item["enabled"])
            assignment = checked(await backend.post("/api/v1/instructor/assignments", headers=teacher, json={
                "group_id": group["id"], "scenario_id": scenario_id, "title": "Synthetic assignment"}))

        origin_headers = {"Origin": "http://127.0.0.1:3000", "X-Voice-UI": "1"}
        clients = [httpx.AsyncClient(base_url=f"http://127.0.0.1:{frontend_port}", headers=origin_headers,
                                     timeout=180, trust_env=False) for _ in students]
        for client, session_token in zip(clients, student_tokens):
            client.cookies.set("training_session", session_token, domain="127.0.0.1", path="/")
        setup_seconds = time.perf_counter() - setup_begin

        async def timed_read(client: httpx.AsyncClient) -> tuple[float, int]:
            await read_gate.wait()
            begin = time.perf_counter()
            try:
                response = await client.get("/api/v1/student/assignments")
                return (time.perf_counter() - begin) * 1000, response.status_code
            except httpx.HTTPError:
                return (time.perf_counter() - begin) * 1000, 0

        read_gate = asyncio.Event()
        read_tasks = [asyncio.create_task(timed_read(client)) for client in clients]
        await asyncio.sleep(0)
        read_wall_begin = time.perf_counter()
        read_gate.set()
        read_results = await asyncio.gather(*read_tasks)
        read_wall = time.perf_counter() - read_wall_begin
        read_latencies = [latency for latency, status in read_results if status == 200]
        read_errors = [status for _, status in read_results if status != 200]

        sessions = []
        for index in range(args.write_sessions):
            response = await clients[index].post("/api/v1/student/sessions", json={
                "scenario_id": scenario_id, "assignment_id": assignment["id"]})
            sessions.append(checked(response))

        offered_rate = args.offered_write_rate
        operation_count = int(round(offered_rate * args.write_seconds))
        per_worker: list[list[tuple[int, float]]] = [[] for _ in sessions]
        for operation in range(operation_count):
            per_worker[operation % len(sessions)].append((operation, operation / offered_rate))
        write_gate = asyncio.Event()
        write_latencies: list[float] = []
        write_completion_times: list[float] = []
        write_errors: list[dict] = []

        async def writer(index: int) -> None:
            session = sessions[index]
            revision = session["revision"]
            card = session["card"]
            await write_gate.wait()
            for operation, offset in per_worker[index]:
                delay = write_wall_begin + offset - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
                body = dict(card)
                body["description"] = f"Synthetic acceptance operation {operation}"
                begin = time.perf_counter()
                try:
                    response = await clients[index].put(f"/api/v1/student/sessions/{session['id']}/card",
                                                        json={"revision": revision, "card": body})
                except httpx.HTTPError as exc:
                    write_errors.append({"status": 0, "operation": operation,
                                         "error": type(exc).__name__})
                    continue
                latency = (time.perf_counter() - begin) * 1000
                if response.status_code == 200:
                    value = response.json()
                    revision, card = value["revision"], value["card"]
                    write_latencies.append(latency)
                    write_completion_times.append(time.perf_counter())
                else:
                    write_errors.append({"status": response.status_code, "operation": operation})

        writer_tasks = [asyncio.create_task(writer(index)) for index in range(len(sessions))]
        await asyncio.sleep(0)
        write_wall_begin = time.perf_counter()
        write_gate.set()
        await asyncio.gather(*writer_tasks)
        write_wall = time.perf_counter() - write_wall_begin
        completed_rate = len(write_latencies) / write_wall if write_wall else 0.0
        completion_span_rate = 0.0
        if len(write_completion_times) > 1:
            completion_span_rate = (len(write_completion_times) - 1) / (
                max(write_completion_times) - min(write_completion_times))

        finish_latencies, finish_errors = [], []
        async def finish(index: int) -> None:
            begin = time.perf_counter()
            try:
                response = await clients[index].post(f"/api/v1/student/sessions/{sessions[index]['id']}/finish")
            except httpx.HTTPError as exc:
                finish_latencies.append((time.perf_counter() - begin) * 1000)
                finish_errors.append(type(exc).__name__)
                return
            finish_latencies.append((time.perf_counter() - begin) * 1000)
            if response.status_code != 200:
                finish_errors.append(response.status_code)
        await asyncio.gather(*(finish(index) for index in range(len(sessions))))

        report_latencies, report_errors = [], []
        async def report(index: int) -> None:
            begin = time.perf_counter()
            try:
                response = await clients[index].get(f"/api/v1/student/sessions/{sessions[index]['id']}/report")
            except httpx.HTTPError as exc:
                report_latencies.append((time.perf_counter() - begin) * 1000)
                report_errors.append(type(exc).__name__)
                return
            report_latencies.append((time.perf_counter() - begin) * 1000)
            if response.status_code != 200:
                report_errors.append(response.status_code)
        report_wall_begin = time.perf_counter()
        await asyncio.gather(*(report(index) for index in range(len(sessions))))
        report_wall = time.perf_counter() - report_wall_begin
        for client in clients:
            await client.aclose()

        result = {
            "generated_at_epoch": round(time.time(), 3),
            "synthetic_only": True,
            "configuration": {
                "backend_workers": 1, "frontend_workers": 1, "database": "temporary SQLite",
                "llm_provider": "mock", "read_users": args.read_users,
                "database": "postgresql" if args.database_url else "sqlite",
                "security_audit": bool(args.security_audit),
                "write_sessions": args.write_sessions, "offered_write_rate_per_second": offered_rate,
                "required_write_rate_per_second": args.required_write_rate,
                "write_duration_seconds": args.write_seconds, "report_deadline_seconds": args.report_deadline,
            },
            "host": {
                "platform": platform.platform(), "python": platform.python_version(),
                "logical_processors": os.cpu_count(), "physical_memory_bytes": physical_memory_bytes(),
            },
            "setup": {"seconds": round(setup_seconds, 3), "included_in_measured_phases": False},
            "reads_through_frontend": {
                **summary(read_latencies), "concurrency": args.read_users,
                "wall_seconds": round(read_wall, 3), "errors": len(read_errors),
                "status_codes": sorted(set(read_errors)), "passed": len(read_errors) == 0,
            },
            "card_writes_through_frontend": {
                **summary(write_latencies), "sessions": args.write_sessions, "requested": operation_count,
                "successful": len(write_latencies), "errors": len(write_errors),
                "wall_seconds_including_drain": round(write_wall, 3),
                "completed_rate_per_second": round(completed_rate, 2),
                "completion_span_rate_per_second": round(completion_span_rate, 2),
                "passed": len(write_errors) == 0 and completed_rate >= args.required_write_rate,
            },
            "finish": {**summary(finish_latencies), "errors": len(finish_errors)},
            "reports_through_frontend": {
                **summary(report_latencies), "concurrency": args.write_sessions,
                "wall_seconds": round(report_wall, 3), "errors": len(report_errors),
                "deadline_seconds": args.report_deadline,
                "passed": len(report_errors) == 0 and bool(report_latencies)
                          and max(report_latencies) / 1000 < args.report_deadline,
            },
            "process_samples": {
                name: {"samples": len(values), "rss_p50_mib": round(statistics.median(values) / 1048576, 2) if values else None,
                       "rss_max_mib": round(max(values) / 1048576, 2) if values else None}
                for name, values in rss_samples.items()
            },
            "network_impairment_30_seconds": {"tested": False, "reason": "No isolated network-shaping facility was used."},
        }
        result["passed"] = all((result["reads_through_frontend"]["passed"],
                                result["card_writes_through_frontend"]["passed"],
                                result["reports_through_frontend"]["passed"]))
        return result
    finally:
        stop_sampling.set()
        if samplers:
            await asyncio.gather(*samplers, return_exceptions=True)
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            stream = getattr(process, "_acceptance_stream", None)
            if stream:
                stream.close()
        if not args.keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-users", type=int, default=100)
    parser.add_argument("--write-sessions", type=int, default=20)
    parser.add_argument("--offered-write-rate", type=float, default=150.0,
                        help="bounded offered rate, with headroom over the required committed rate")
    parser.add_argument("--required-write-rate", type=float, default=100.0)
    parser.add_argument("--write-seconds", type=float, default=1.0,
                        help="bounded offered-load burst duration")
    parser.add_argument("--report-deadline", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--database-url", default=None,
                        help="PostgreSQL URL to measure the deployed database instead of temporary SQLite")
    parser.add_argument("--security-audit", action="store_true",
                        help="Enable SECURITY_AUDIT_DIR, as the Compose deployment does")
    args = parser.parse_args()
    if args.read_users < args.write_sessions or min(args.write_sessions, args.write_seconds,
                                                     args.offered_write_rate, args.required_write_rate) <= 0:
        parser.error("counts, rates and duration must be positive; read-users must cover write-sessions")
    result = asyncio.run(run(args))
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
