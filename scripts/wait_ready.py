#!/usr/bin/env python3
"""Дождаться готовности стенда: моки отвечают, Temporal SERVING, namespace default создан."""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEADLINE = 150

# системный прокси (xray и т.п.) не должен перехватывать запросы к localhost
for _var in ("NO_PROXY", "no_proxy"):
    _cur = [p for p in os.environ.get(_var, "").split(",") if p]
    os.environ[_var] = ",".join(_cur + [a for a in ("localhost", "127.0.0.1", "::1") if a not in _cur])


def mocks_ready():
    with urllib.request.urlopen("http://localhost:8100/api/orders", timeout=2) as r:
        return r.status == 200


def _admin(*cmd):
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "temporal-admin-tools"] + list(cmd),
        cwd=str(ROOT), capture_output=True, text=True, timeout=20)


def temporal_ready():
    r = _admin("temporal", "operator", "cluster", "health")
    return r.returncode == 0 and "SERVING" in r.stdout


def namespace_ready():
    return _admin("temporal", "operator", "namespace", "describe", "default").returncode == 0


def main():
    t0 = time.monotonic()
    for name, fn in (("моки (localhost:8100)", mocks_ready),
                     ("temporal (localhost:7233)", temporal_ready),
                     ("namespace default", namespace_ready)):
        while True:
            try:
                if fn():
                    print("  ✔ " + name)
                    break
            except Exception:
                pass
            if time.monotonic() - t0 > DEADLINE:
                print("❌ '{}' не готов за {}с. Диагностика: make logs".format(name, DEADLINE))
                return 1
            time.sleep(2)
    try:
        with urllib.request.urlopen("http://localhost:8233", timeout=3) as r:
            if r.status == 200:
                print("  ✔ temporal ui (localhost:8233)")
    except Exception:
        print("  ⚠ UI (localhost:8233) пока не отвечает — не критично")
    print("✅ Стенд готов")
    return 0


if __name__ == "__main__":
    sys.exit(main())
