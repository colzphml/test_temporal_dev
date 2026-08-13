#!/usr/bin/env python3
"""Создание воркспейса прогона: venv с зависимостями, TASK.md, Makefile, CLAUDE.md.

Воркспейс — единственное место, где работает агент-испытуемый. TASK.md собирается
из intro + SPEC + вариантной части, чтобы общая часть не расходилась между вариантами.
"""
import argparse
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, choices=["python", "temporal"])
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
        shutil.rmtree(str(ws))

    py = find_python()
    if py is None:
        print("❌ Нужен Python ≥ 3.10 (brew install python@3.13)")
        return 1

    sol.mkdir(parents=True, exist_ok=True)
    print("… создаю venv ({})".format(py))
    if subprocess.run([py, "-m", "venv", str(ws / ".venv")]).returncode != 0:
        print("❌ Не удалось создать venv")
        return 1
    req = SPEC / "requirements-{}.txt".format(args.variant)
    print("… ставлю зависимости из spec/{}".format(req.name))
    r = subprocess.run([str(ws / ".venv" / "bin" / "pip"), "install", "-q",
                        "--disable-pip-version-check", "-r", str(req)])
    if r.returncode != 0:
        print("❌ pip install не удался (нет сети?). Повтори: make workspace VARIANT={} FORCE=1".format(args.variant))
        return 1

    task = "\n\n---\n\n".join([
        (SPEC / "intro.md").read_text(),
        (SPEC / "SPEC.md").read_text(),
        (SPEC / "variant_{}.md".format(args.variant)).read_text(),
    ])
    (ws / "TASK.md").write_text(task)
    (ws / "CLAUDE.md").write_text(
        (SPEC / "workspace_claude.md").read_text().replace("{{VARIANT}}", args.variant))
    (ws / "Makefile").write_text(
        (SPEC / "workspace_makefile").read_text().replace("{{VARIANT}}", args.variant))
    (ws / ".gitignore").write_text(".venv/\n__pycache__/\n")

    print("""
✅ Воркспейс готов: runs/{v}

Дальше по протоколу (EXPERIMENT.md):
  1. В корне репо:  make begin VARIANT={v} MODEL="<модель>"
  2. Новая сессия агента:  cd runs/{v} && claude --permission-mode acceptEdits
  3. Вставь ровно этот промпт (одинаковый для обоих вариантов):
     Прочитай TASK.md в текущей папке и выполни задачу полностью. Готово, когда make verify зелёный.
""".format(v=args.variant))
    return 0


if __name__ == "__main__":
    sys.exit(main())
