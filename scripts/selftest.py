#!/usr/bin/env python3
"""Селфтест харнесса: чекер принимает эталонные решения и ловит испорченные.

Фаза 1: RED (пустое решение) → GREEN python → 2 негативных кейса (REF_BUG) →
GREEN temporal → повторный GREEN temporal (сброс между прогонами).
Фаза 2 (SIGKILL + рестарт): GREEN python2 → NEGATIVE random_key →
GREEN temporal2 → NEGATIVE stamped_ids.
Таймер в этом режиме отключён (HARNESS_SELFTEST=1), results/ не засоряется.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, HARNESS_SELFTEST="1")


def sh(args):
    print("\n$ " + " ".join(str(a) for a in args))
    return subprocess.run([str(a) for a in args], cwd=str(ROOT), env=ENV).returncode


def verify(variant):
    """Прогон verify с захватом вывода: (код возврата, полный текст)."""
    args = [sys.executable, str(ROOT / "scripts" / "verify.py"), "--variant", variant]
    print("\n$ " + " ".join(args))
    r = subprocess.run(args, cwd=str(ROOT), env=ENV, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    print(out)
    return r.returncode, out


def failed_with(result, marker):
    """Провал именно по ожидаемой причине: код != 0 И маркер в отчёте чекера."""
    rc, out = result
    if rc == 0:
        return False
    if marker not in out:
        print("⚠ verify упал, но БЕЗ ожидаемого маркера '{}' — причина не та".format(marker))
        return False
    return True


def workspace(variant, phase=1):
    if sh([sys.executable, ROOT / "scripts" / "workspace.py", "--variant", variant,
           "--phase", str(phase), "--force"]) != 0:
        print("❌ селфтест: не удалось создать воркспейс " + variant)
        sys.exit(1)


def put_reference(name, variant):
    src = ROOT / "reference" / name
    dst = ROOT / "runs" / variant / "solution"
    for f in src.iterdir():
        if f.is_file():
            shutil.copy(str(f), str(dst / f.name))


def passed_with(result, marker):
    """Зелёный verify, и в выводе есть маркер (например, что разрыв пережит)."""
    rc, out = result
    if rc != 0:
        return False
    if marker not in out:
        print("⚠ verify прошёл, но в выводе нет маркера '{}'".format(marker))
        return False
    return True


def clear_solution(variant):
    dst = ROOT / "runs" / variant / "solution"
    if not dst.exists():
        return
    for f in dst.iterdir():
        if f.is_dir():
            shutil.rmtree(str(f))
        else:
            f.unlink()


def step(title, cond, results):
    print("\n{} СЕЛФТЕСТ: {}".format("✅" if cond else "❌", title))
    results.append(cond)


def expect_fail(why):
    print("\n┌─ ВНИМАНИЕ: следующий verify ДОЛЖЕН упасть — это проверка чекера")
    print("└─ ({}). «VERIFY FAILED» ниже — запланированный.".format(why))


def main():
    results = []

    if sh(["make", "up"]) != 0:
        print("❌ селфтест: make up не прошёл")
        return 1

    # ── ФАЗА 1 ─────────────────────────────────────────────────────
    # RED: решение-пустышка обязано провалить verify по причине «пустой журнал»
    workspace("python")
    sol = ROOT / "runs" / "python" / "solution"
    (sol / "run.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    expect_fail("пустое решение не должно проходить")
    step("пустое решение падает (RED)", failed_with(verify("python"), "Журнал пуст"), results)

    # GREEN: эталон на чистом питоне проходит
    clear_solution("python")
    put_reference("python", "python")
    step("эталон python проходит (GREEN)", verify("python")[0] == 0, results)

    # NEGATIVE 1: «забыли» refund при провале доставки — чекер должен поймать
    (sol / "REF_BUG").write_text("no_refund\n")
    expect_fail("в эталон подсажен баг: не делается refund")
    step("чекер ловит отсутствие refund (NEGATIVE)",
         failed_with(verify("python"), "нет возврата денег"), results)

    # NEGATIVE 2: новый Idempotency-Key на каждую попытку → двойное списание
    (sol / "REF_BUG").write_text("new_key_per_attempt\n")
    expect_fail("в эталон подсажен баг: новый Idempotency-Key на каждую попытку")
    step("чекер ловит двойное списание (NEGATIVE)",
         failed_with(verify("python"), "ДВОЙНОЕ СПИСАНИЕ"), results)
    (sol / "REF_BUG").unlink()

    # GREEN temporal + повторный прогон (сброс/зачистка между verify работает)
    workspace("temporal")
    put_reference("temporal", "temporal")
    step("эталон temporal проходит (GREEN)", verify("temporal")[0] == 0, results)
    step("повторный verify temporal проходит (reset ok)", verify("temporal")[0] == 0, results)

    # ── ФАЗА 2: SIGKILL посреди батча + рестарт ────────────────────
    workspace("python", phase=2)
    sol = ROOT / "runs" / "python" / "solution"
    put_reference("python2", "python")
    step("фаза 2: эталон python переживает SIGKILL (GREEN)",
         passed_with(verify("python"), "пережит"), results)

    (sol / "REF_BUG").write_text("random_key\n")
    expect_fail("в эталон подсажен баг: случайный ключ при каждом старте процесса")
    step("фаза 2: чекер ловит двойное списание после рестарта (NEGATIVE)",
         failed_with(verify("python"), "ДВОЙНОЕ СПИСАНИЕ"), results)
    (sol / "REF_BUG").unlink()

    workspace("temporal", phase=2)
    tsol = ROOT / "runs" / "temporal" / "solution"
    put_reference("temporal2", "temporal")
    step("фаза 2: эталон temporal переживает SIGKILL (GREEN)",
         passed_with(verify("temporal"), "пережит"), results)

    (tsol / "REF_BUG").write_text("stamped_ids\n")
    expect_fail("в эталон подсажен баг: workflow id меняется при рестарте")
    step("фаза 2: чекер ловит дубли workflow после рестарта (NEGATIVE)",
         failed_with(verify("temporal"), "ДВОЙНОЕ СПИСАНИЕ"), results)
    (tsol / "REF_BUG").unlink()

    # ── ФАЗА 3: то же, но TASK.md без готового кода ────────────────
    workspace("python", phase=3)
    put_reference("python2", "python")
    task_p = (ROOT / "runs" / "python" / "TASK.md").read_text()
    step("фаза 3: в TASK.md (python) нет готового кода",
         "```python" not in task_p and "```bash" not in task_p, results)
    step("фаза 3: эталон python проходит (GREEN)",
         passed_with(verify("python"), "пережит"), results)

    workspace("temporal", phase=3)
    put_reference("temporal2", "temporal")
    task_t = (ROOT / "runs" / "temporal" / "TASK.md").read_text()
    step("фаза 3: в TASK.md (temporal) нет готового кода",
         "```python" not in task_t and "```bash" not in task_t, results)
    step("фаза 3: эталон temporal проходит (GREEN)",
         passed_with(verify("temporal"), "пережит"), results)

    # прибраться: чистые воркспейсы фазы 1 по умолчанию, без эталонов внутри
    workspace("python")
    workspace("temporal")

    print("\n" + "=" * 60)
    if all(results):
        print("✅ СЕЛФТЕСТ ПРОЙДЕН ({}/{}): стенд готов к эксперименту".format(len(results), len(results)))
        print("   5 «VERIFY FAILED» выше — запланированные (RED и четыре NEGATIVE-кейса).")
        print("   Воркспейсы пересозданы чистыми (фаза 1). Перед прогоном: make workspace VARIANT=... [PHASE=2|3] FORCE=1")
        return 0
    print("❌ СЕЛФТЕСТ НЕ ПРОЙДЕН ({} из {} ок)".format(sum(results), len(results)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
