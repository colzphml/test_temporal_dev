#!/usr/bin/env python3
"""Замер времени прогона эксперимента.

Файл результата: results/<variant>.json. Завершённые прогоны при новом
`begin` уезжают в results/history/. Успешный verify фиксирует конец прогона.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
HISTORY = RESULTS / "history"


def _now():
    return datetime.now().astimezone()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _path(variant):
    return RESULTS / (variant + ".json")


def _load(variant):
    p = _path(variant)
    if p.exists():
        return json.loads(p.read_text())
    return None


def _save(variant, data):
    RESULTS.mkdir(parents=True, exist_ok=True)
    _path(variant).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def cmd_begin(args):
    data = _load(args.variant)
    if data and not data.get("end"):
        print("⏱ Прогон '{}' уже начат {} — продолжаем (no-op).".format(args.variant, data["begin"]))
        return 0
    if data and data.get("end"):
        HISTORY.mkdir(parents=True, exist_ok=True)
        stamp = data["end"].replace(":", "").replace("-", "").replace("+", "_")
        _path(args.variant).rename(HISTORY / "{}-{}.json".format(args.variant, stamp))
        print("📦 Прошлый завершённый результат перенесён в results/history/")
    data = {
        "variant": args.variant,
        "model": args.model or "",
        "begin": _iso(_now()),
        "attempts": [],
        "end": None,
        "duration_sec": None,
    }
    _save(args.variant, data)
    print("⏱ Старт прогона '{}' зафиксирован: {}".format(args.variant, data["begin"]))
    return 0


def cmd_attempt(args):
    data = _load(args.variant)
    if data is None:
        print("⚠ timer: `make begin` не выполнялся — попытка не записана, длительность не будет измерена")
        return 0
    data["attempts"].append({"ts": _iso(_now()), "ok": bool(args.ok)})
    if args.ok and not data.get("end"):
        end = _now()
        begin = datetime.fromisoformat(data["begin"])
        data["end"] = _iso(end)
        data["duration_sec"] = round((end - begin).total_seconds(), 1)
        mins, secs = divmod(int(data["duration_sec"]), 60)
        fails = sum(1 for a in data["attempts"] if not a["ok"])
        print("🏁 ПРОГОН '{}' ЗАВЕРШЁН: {} мин {} с; неудачных verify до успеха: {}".format(
            args.variant, mins, secs, fails))
    _save(args.variant, data)
    return 0


def _fmt_row(d):
    dur = d.get("duration_sec")
    if dur is None:
        dur_s = "— (не завершён)"
    else:
        mins, secs = divmod(int(dur), 60)
        dur_s = "{}:{:02d}".format(mins, secs)
    attempts = d.get("attempts", [])
    fails = sum(1 for a in attempts if not a["ok"])
    return "| {} | {} | {} | {} | {} | {} |".format(
        d["variant"], d.get("model") or "?", d["begin"], dur_s, len(attempts), fails)


def cmd_report(args):
    rows = []
    if HISTORY.exists():
        for p in sorted(HISTORY.glob("*.json")):
            rows.append(_fmt_row(json.loads(p.read_text())))
    for v in ("python", "temporal"):
        d = _load(v)
        if d:
            rows.append(_fmt_row(d))
    if not rows:
        print("Результатов пока нет. Перед прогоном: make begin VARIANT=python|temporal")
        return 0
    header = ("| вариант | модель | старт | длительность | verify всего | неудачных |\n"
              "|---|---|---|---|---|---|")
    table = header + "\n" + "\n".join(rows)
    print(table)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "results.md").write_text(
        "# Результаты эксперимента\n\n" + table +
        "\n\nТокены/стоимость сессии допиши вручную (команда /cost в Claude Code).\n")
    print("\n(таблица сохранена в results/results.md)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("begin")
    b.add_argument("--variant", required=True, choices=["python", "temporal"])
    b.add_argument("--model", default="")
    a = sub.add_parser("attempt")
    a.add_argument("--variant", required=True, choices=["python", "temporal"])
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--ok", dest="ok", action="store_true")
    g.add_argument("--fail", dest="ok", action="store_false")
    sub.add_parser("report")
    args = ap.parse_args()
    if args.cmd is None:
        ap.print_help()
        return 2
    return {"begin": cmd_begin, "attempt": cmd_attempt, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
