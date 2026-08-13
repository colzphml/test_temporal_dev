#!/usr/bin/env python3
"""Проверка окружения перед make up. Пишет, что именно починить."""
import shutil
import socket
import subprocess
import sys


def main():
    if shutil.which("docker") is None:
        print("❌ docker не найден. Поставь Docker Desktop: https://www.docker.com/products/docker-desktop/")
        return 1
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        print("❌ Docker-демон не отвечает. Запусти Docker Desktop: open -a Docker")
        return 1
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        print("❌ docker compose v2 недоступен (обнови Docker Desktop)")
        return 1

    found = None
    for cand in ("python3.13", "python3.12", "python3.11", "python3.10"):
        if shutil.which(cand):
            found = cand
            break
    if not found:
        print("⚠ Не найден Python ≥ 3.10 — понадобится для воркспейсов: brew install python@3.13")

    for port in (7233, 8233, 8100):
        s = socket.socket()
        s.settimeout(0.3)
        busy = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        if busy:
            print("⚠ Порт {} уже занят — если это не наш стенд, make up упадёт".format(port))

    print("✅ preflight ok" + (" (python для воркспейсов: {})".format(found) if found else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
