#!/usr/bin/env python3
"""Создание воркспейса прогона: venv с зависимостями, TASK.md, Makefile, CLAUDE.md.

Воркспейс — единственное место, где работает агент-испытуемый. TASK.md собирается
из intro + SPEC (+ фаза 2) + вариантной части, чтобы общая часть не расходилась
между вариантами. Файл PHASE сообщает verify, какую фазу гонять.

venv переиспользуется между пересозданиями, если requirements не менялись.
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "spec"


def find_python():
    for cand in ("python3.13", "python3.12", "python3.11", "python3.10"):
        p = shutil.which(cand)
        if p:
            return p
    p = shutil.which("python3")
    if p:
        r = subprocess.run([p, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"])
        if r.returncode == 0:
            return p
    return None


def ensure_venv(ws, variant):
    """Создать venv или переиспользовать существующий при неизменных requirements."""
    req = SPEC / "requirements-{}.txt".format(variant)
    req_hash = hashlib.sha256(req.read_bytes()).hexdigest()
    venv = ws / ".venv"
    hash_file = venv / "req.sha256"
    if venv.exists() and hash_file.exists() and hash_file.read_text().strip() == req_hash:
        print("… venv уже актуален (requirements не менялись)")
        return True
    if venv.exists():
        shutil.rmtree(str(venv))
    py = find_python()
    if py is None:
        print("❌ Нужен Python ≥ 3.10 (brew install python@3.13)")
        return False
    print("… создаю venv ({})".format(py))
    if subprocess.run([py, "-m", "venv", str(venv)]).returncode != 0:
        print("❌ Не удалось создать venv")
        return False
    print("… ставлю зависимости из spec/{}".format(req.name))
    r = subprocess.run([str(venv / "bin" / "pip"), "install", "-q",
                        "--disable-pip-version-check", "-r", str(req)])
    if r.returncode != 0:
        print("❌ pip install не удался (нет сети?). Повтори команду workspace")
        return False
    hash_file.write_text(req_hash)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, choices=["python", "temporal"])
    ap.add_argument("--phase", type=int, default=1, choices=[1, 2])
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    ws = ROOT / "runs" / args.variant
    sol = ws / "solution"
    if sol.exists() and any(sol.iterdir()) and not args.force:
        print("❌ В {} уже лежит решение.".format(sol))
        print("   Сохрани его:      make archive VARIANT={}".format(args.variant))
        print("   Затем пересоздай: make workspace VARIANT={} FORCE=1".format(args.variant))
        return 1
    if ws.exists() and args.force:
        for child in ws.iterdir():
            if child.name == ".venv":
                continue  # venv дорогой — переиспользуем, если requirements те же
            if child.is_dir():
                shutil.rmtree(str(child))
            else:
                child.unlink()

    sol.mkdir(parents=True, exist_ok=True)
    if not ensure_venv(ws, args.variant):
        return 1

    parts = [(SPEC / "intro.md").read_text(), (SPEC / "SPEC.md").read_text()]
    if args.phase == 2:
        parts.append((SPEC / "phase2.md").read_text())
    parts.append((SPEC / "variant_{}.md".format(args.variant)).read_text())
    if args.phase == 2:
        parts.append((SPEC / "phase2_{}.md".format(args.variant)).read_text())
    (ws / "TASK.md").write_text("\n\n---\n\n".join(parts))
    (ws / "PHASE").write_text(str(args.phase) + "\n")
    (ws / "CLAUDE.md").write_text(
        (SPEC / "workspace_claude.md").read_text().replace("{{VARIANT}}", args.variant))
    (ws / "Makefile").write_text(
        (SPEC / "workspace_makefile").read_text().replace("{{VARIANT}}", args.variant))
    (ws / ".gitignore").write_text(".venv/\n__pycache__/\n")

    print("""
✅ Воркспейс готов: runs/{v} (фаза {p})

Дальше по протоколу (EXPERIMENT.md):
  1. В корне репо:  make begin VARIANT={v} MODEL="<модель>"
  2. Новая сессия агента:  cd runs/{v} && claude --permission-mode acceptEdits
  3. Вставь ровно этот промпт (одинаковый для обоих вариантов):
     Прочитай TASK.md в текущей папке и выполни задачу полностью. Готово, когда make verify зелёный.
""".format(v=args.variant, p=args.phase))
    return 0


if __name__ == "__main__":
    sys.exit(main())
