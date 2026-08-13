#!/usr/bin/env python3
"""make verify: сброс моков → запуск solution/run.sh → проверка инвариантов чекером.

Фаза 1: одиночный запуск решения.
Фаза 2 (в воркспейсе лежит файл PHASE со значением 2): решение убивается SIGKILL
после нескольких успешных списаний и перезапускается — проверка живучести.

Критерий готовности решения в эксперименте. Идемпотентен, можно гонять сколько
угодно. Каждая попытка логируется таймером (кроме режима селфтеста харнесса).
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOCKS = "http://localhost:8100"
RUN_TIMEOUT = 150        # жёсткий предел на один запуск run.sh
KILL_AFTER_CHARGES = 3   # фаза 2: SIGKILL после стольких успешных списаний
KILL_DEADLINE = 60       # фаза 2: сколько ждать достижения точки SIGKILL
SELFTEST = os.environ.get("HARNESS_SELFTEST") == "1"


def _no_proxy_for_localhost():
    """Системный прокси macOS (например, xray) не должен перехватывать localhost:
    python-клиенты (urllib, httpx) читают его из системных настроек, где
    127.0.0.1 часто нет в исключениях. NO_PROXY решает это и для нас, и для
    дочернего решения."""
    add = ["localhost", "127.0.0.1", "::1"]
    for var in ("NO_PROXY", "no_proxy"):
        cur = [p for p in os.environ.get(var, "").split(",") if p]
        os.environ[var] = ",".join(cur + [a for a in add if a not in cur])


_no_proxy_for_localhost()


def http(method, path, timeout=5):
    req = urllib.request.Request(MOCKS + path, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def compose(*args, **kw):
    timeout = kw.get("timeout", 30)
    return subprocess.run(
        ["docker", "compose"] + list(args),
        cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)


def terminate_stale_workflows():
    """Прибить незавершённые workflow прошлых попыток, чтобы после сброса моков
    они не ожили и не испортили журнал текущего прогона."""
    try:
        r = compose("exec", "-T", "temporal-admin-tools",
                    "temporal", "workflow", "list",
                    "-q", "ExecutionStatus='Running'", "-o", "json")
    except subprocess.TimeoutExpired:
        print("⚠ temporal workflow list не ответил — пропускаю зачистку")
        return
    if r.returncode != 0:
        print("⚠ не удалось получить список workflow (не мешает verify):", r.stderr.strip()[:200])
        return
    ids = sorted(set(re.findall(r'"workflowId"\s*:\s*"([^"]+)"', r.stdout)))
    for wid in ids:
        try:
            compose("exec", "-T", "temporal-admin-tools",
                    "temporal", "workflow", "terminate", "-w", wid,
                    "--reason", "harness-verify-reset", timeout=20)
        except subprocess.TimeoutExpired:
            pass
    if ids:
        print("⚠ прервано незавершённых workflow с прошлых запусков: {}".format(len(ids)))


def timer(*args):
    if SELFTEST:
        return
    subprocess.run([sys.executable, str(ROOT / "scripts" / "timer.py")] + list(args), cwd=str(ROOT))


def launch(sol, env):
    return subprocess.Popen(["bash", "run.sh"], cwd=str(sol), env=env, start_new_session=True)


def kill_group(proc, sig):
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except Exception:
        pass


def wait_full(proc):
    """Дождаться завершения запуска: (rc, timed_out)."""
    try:
        return proc.wait(timeout=RUN_TIMEOUT), False
    except subprocess.TimeoutExpired:
        kill_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            kill_group(proc, signal.SIGKILL)
            proc.wait()
        return -1, True


def total_charges():
    state = http("GET", "/admin/state")
    return sum(len(o["payments"]["successful_charges"]) for o in state["orders"])


def wait_kill_condition(proc):
    """Ждать точку SIGKILL (фаза 2): вернуть ('ok', elapsed) | ('died', rc) | ('timeout', None)."""
    t0 = time.monotonic()
    while True:
        rc = proc.poll()
        if rc is not None:
            return "died", rc
        try:
            if total_charges() >= KILL_AFTER_CHARGES:
                return "ok", time.monotonic() - t0
        except Exception:
            pass
        if time.monotonic() - t0 > KILL_DEADLINE:
            return "timeout", None
        time.sleep(0.3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, choices=["python", "temporal"])
    args = ap.parse_args()

    ws = ROOT / "runs" / args.variant
    sol = ws / "solution"
    if not ws.exists():
        print("❌ Воркспейс {} не существует. Сначала: make workspace VARIANT={}".format(ws, args.variant))
        return 2
    phase = 1
    phase_file = ws / "PHASE"
    if phase_file.exists():
        try:
            phase = int(phase_file.read_text().strip() or "1")
        except ValueError:
            phase = 1
    if not (sol / "run.sh").exists():
        print("❌ Нет файла solution/run.sh — это точка входа решения (контракт в TASK.md).")
        timer("attempt", "--variant", args.variant, "--fail")
        return 1

    try:
        http("GET", "/api/orders")
    except Exception as e:
        print("❌ Моки недоступны на {} ({}). Подними стенд: make up".format(MOCKS, e))
        return 2

    if args.variant == "temporal":
        terminate_stale_workflows()

    http("POST", "/admin/reset")

    env = dict(os.environ)
    env.update({
        "MOCKS_URL": MOCKS,
        "TEMPORAL_ADDRESS": "localhost:7233",
        "TEMPORAL_NAMESPACE": "default",
        "RUN_ID": uuid.uuid4().hex[:8],
    })

    t_start = time.monotonic()
    timed_out = False

    if phase == 2:
        print("… моки сброшены; фаза 2: запускаю решение, убью его после {} списаний".format(KILL_AFTER_CHARGES))
        proc = launch(sol, env)
        status, val = wait_kill_condition(proc)
        if status == "died":
            print("❌ run.sh завершился (код {}) до точки SIGKILL — решение упало слишком рано".format(val))
            rc = val if val != 0 else 1
        elif status == "timeout":
            kill_group(proc, signal.SIGKILL)
            proc.wait()
            print("❌ За {}с решение не добралось до {} успешных списаний — нечего убивать".format(
                KILL_DEADLINE, KILL_AFTER_CHARGES))
            rc = 1
        else:
            kill_group(proc, signal.SIGKILL)
            proc.wait()
            http("POST", "/admin/mark-kill")  # отметка строго после смерти процесса
            print("💀 SIGKILL через {:.1f}с (успешных списаний ≥ {}). Рестарт через 1с…".format(
                val, KILL_AFTER_CHARGES))
            time.sleep(1)
            proc = launch(sol, env)
            rc, timed_out = wait_full(proc)
    else:
        print("… моки сброшены, запускаю solution/run.sh")
        proc = launch(sol, env)
        rc, timed_out = wait_full(proc)

    dur = time.monotonic() - t_start
    print("… run.sh завершился с кодом {} за {:.1f}с".format(rc, dur))

    check = http("GET", "/admin/check", timeout=15)
    print("\n──────── отчёт чекера ────────")
    print(check["report"])
    print("──────────────────────────────\n")

    ok = bool(check["ok"]) and rc == 0 and not timed_out
    if timed_out:
        print("❌ run.sh не завершился за {}с и был убит".format(RUN_TIMEOUT))
    elif rc != 0:
        print("❌ run.sh завершился с ненулевым кодом {} (контракт: 0 при успехе)".format(rc))

    if ok:
        print("✅ VERIFY PASSED")
        timer("attempt", "--variant", args.variant, "--ok")
        return 0
    print("❌ VERIFY FAILED")
    timer("attempt", "--variant", args.variant, "--fail")
    return 1


if __name__ == "__main__":
    sys.exit(main())
