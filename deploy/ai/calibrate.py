# language: Python, file: deploy/ai/calibrate.py, target: Ubuntu 24.04, host 77.90.6.247
"""Калибровка классификатора: история жалоб через модель, метрики качества.

Читает жалобы с текстом из боевой БД (только SELECT), гоняет их через
полный боевой цикл (до MAX_ATTEMPTS попыток и гейт сводки из watcher) и
считает: успех с первой попытки и после ретраев, согласие категории с
причиной жалобы, распределение severity, задержки. На выборке прогоняется
старый v4-промпт («до») -- ради замера эха шаблона. В report_ai ничего не
пишется: отчёт идёт в stdout и в JSON-файл.

Вотчер на время прогона остановить (иначе его модель и эта подерутся за
порт 18080): systemctl stop llm-report-watcher

Запуск: nice -n 10 python3 calibrate.py [--legacy 12] [--limit 200]
"""

import argparse
import json
import time
from pathlib import Path

import watcher

# Промпт v4 -- как стоял до калибровки; нужен только для честного «до».
LEGACY_PROMPT = (
    "Ты -- модератор анонимного чата. Тебе дадут причину жалобы и диалог. "
    "Выбери РОВНО ОДНО значение category: spam (реклама), insult (оскорбления), "
    "adult (контент 18+), minor (несовершеннолетние), fraud (мошенничество), "
    "other (прочее). Выбери РОВНО ОДНО значение severity: "
    "low -- мелочь, formalностями, medium -- стоит проверить, "
    "high -- серьёзно, нужен бан. summary -- две фразы по-русски: "
    "что случилось и что сделать модератору. "
    "Пример: причина «Спам/реклама», в диалоге «заходи в @signals, "
    "плюс 5% каждый день» → ответ "
    '{"category": "spam", "severity": "medium", "summary": "Приглашение в '
    'канал с рекламой -- нежелательная реклама; предупредить или отклонить."} '
    "Отвечай только одним JSON-объектом, без markdown и пояснений."
)

EXPECTED = {
    "Контент 18+": "adult",
    "Оскорбления": "insult",
    "Спам/реклама": "spam",
    "Мошенничество": "fraud",
    "Подозрение на несовершеннолетнего": "minor",
    "Другое": "other",
}


def load_history(limit):
    sql = (
        "SELECT row_to_json(t) FROM ("
        " SELECT id, reason, coalesce(message_content, '') AS content"
        " FROM reports"
        " WHERE coalesce(message_content, '') <> ''"
        f" ORDER BY id LIMIT {int(limit)}) t"
    )
    raw = watcher.psql(sql)
    if raw is None:
        raise SystemExit("БД недоступна")
    rows = []
    for line in raw.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def run_case(row, prompt):
    """Боевой цикл: до MAX_ATTEMPTS попыток через гейт сводки."""
    reason = row.get("reason", "")
    content = row.get("content", "")
    verdict = None
    attempt = 0
    seconds = 0.0

    def _passed(v):
        return bool(v and watcher.passes_filters(v["summary"]))

    while attempt < watcher.MAX_ATTEMPTS and not _passed(verdict):
        attempt += 1
        started = time.time()
        verdict = watcher.ask_model(reason, content, prompt=prompt)
        seconds = round(time.time() - started, 1)
    ok = _passed(verdict)
    return {
        "id": row.get("id"),
        "reason": reason,
        "ok": ok,
        "outcome": "ok" if ok else ("summary-fail" if verdict else "parse-fail"),
        "attempts": attempt,
        "category": verdict["category"] if verdict else None,
        "severity": verdict["severity"] if verdict else None,
        "summary": verdict["summary"] if verdict else None,
        "seconds": seconds,
    }


def run_legacy(row):
    """v4: одна попытка, без схемы и гейта; интересно только эхо."""
    verdict = watcher.ask_model(
        row.get("reason", ""), row.get("content", ""), prompt=LEGACY_PROMPT
    )
    summary = verdict["summary"] if verdict else ""
    return {
        "id": row.get("id"),
        "ok": verdict is not None,
        "echo": bool(summary and watcher.looks_like_echo(summary)),
        "degenerate": bool(summary and watcher.looks_degenerate(summary)),
        "summary": summary or None,
    }


def metrics_new(cases):
    return {
        "total": len(cases),
        "ok": sum(1 for c in cases if c["ok"]),
        "ok_first_try": sum(1 for c in cases if c["ok"] and c["attempts"] == 1),
        "category_agree": sum(
            1
            for c in cases
            if c["ok"] and EXPECTED.get(c["reason"]) == c["category"]
        ),
        "fails": [f"#{c['id']}:{c['outcome']}" for c in cases if not c["ok"]],
    }


def metrics_legacy(cases):
    return {
        "total": len(cases),
        "valid": sum(1 for c in cases if c["ok"]),
        "echo": sum(1 for c in cases if c["echo"]),
        "degenerate": sum(1 for c in cases if c["degenerate"]),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", type=int, default=12)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--out", default="/opt/llm/calibration.json")
    args = parser.parse_args()

    history = load_history(args.limit)
    if not history:
        raise SystemExit("история с текстом пуста")
    print(f"история: {len(history)} жалоб с текстом")
    if not watcher.ensure_llama():
        raise SystemExit("llama-server не поднялся")

    cases = [run_case(row, watcher.SYSTEM_PROMPT) for row in history]
    legacy = []
    if args.legacy > 0:
        step = max(1, len(history) // args.legacy)
        sample = history[::step][: args.legacy]
        legacy = [run_legacy(row) for row in sample]
        print(f"выборка для «до»: {len(sample)}")

    if watcher._llama is not None and watcher._llama.poll() is None:
        watcher._llama.terminate()

    report = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "new": metrics_new(cases),
        "legacy": metrics_legacy(legacy),
        "cases": cases,
        "legacy_cases": legacy,
    }
    Path(args.out).write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("== v5 (json_schema + гейт):", json.dumps(report["new"], ensure_ascii=False))
    print("== v4 (до):", json.dumps(report["legacy"], ensure_ascii=False))

    sev = {}
    for case in cases:
        if case["ok"]:
            sev[case["severity"]] = sev.get(case["severity"], 0) + 1
    print("== severity (по успешным):", json.dumps(sev, ensure_ascii=False))

    by_reason = {}
    for case in cases:
        agg = by_reason.setdefault(case["reason"], {"n": 0, "agree": 0, "miss": []})
        agg["n"] += 1
        if case["ok"] and case["category"] == EXPECTED.get(case["reason"]):
            agg["agree"] += 1
        elif case["ok"]:
            agg["miss"].append(f"#{case['id']}:{case['category']}")
    for reason, agg in sorted(by_reason.items()):
        print(f"== {reason}: {agg['agree']}/{agg['n']} miss={agg['miss'][:8]}")

    print("== примеры v5 (свежие):")
    for case in cases[-12:]:
        print(
            f"  #{case['id']} [{case['reason']}] -> {case['category']}/"
            f"{case['severity']} попыток={case['attempts']} {case['seconds']}c: "
            f"{case['summary']}"
        )
    print("== примеры v4 (выборка):")
    for case in legacy[:8]:
        print(f"  v4 #{case['id']} эхо={case['echo']}: {case['summary']}")


if __name__ == "__main__":
    main()
