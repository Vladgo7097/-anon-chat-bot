# language: Python, file: deploy/ai/watcher.py, target: Ubuntu 24.04, host 77.90.6.247
"""Вотчер жалоб: классифицирует их локальной Qwen3-0.6B и пишет в report_ai.

Стоит на хосте, не в контейнере: ходит в Postgres через `docker exec psql`,
чтобы не трогать стек бота, а llama-server поднимает по требованию -- модель
стартует под первую жалобу в пачке и умирает через IDLE_SECONDS молчания,
иначе ~900 МБ RSS висят в RAM постоянно. Классифицируются только
pending-жалобы без строки в report_ai; после MAX_ATTEMPTS неудач пишется
служебный вердикт «не разобрано», чтобы очередь не зависала навсегда.
"""

import json
import logging
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

LLAMA = "/opt/llm/llama-b11706/llama-server"
MODEL = "/opt/llm/Qwen3-0.6B-Q4_K_M.gguf"
LLAMA_LOG = "/opt/llm/llama.log"
BASE = "http://127.0.0.1:18080"
PORT = "18080"
POLL_SECONDS = 15
IDLE_SECONDS = 180
HEALTH_TIMEOUT = 90
BATCH = 4
MAX_ATTEMPTS = 3
CONTENT_LIMIT = 1500

CATEGORIES = {"spam", "insult", "adult", "minor", "fraud", "other"}
SEVERITIES = {"low", "medium", "high"}

SYSTEM_PROMPT = (
    "Ты -- модератор анонимного чата. По жалобе поставь квалификацию. "
    "Ответь СТРОГО одним JSON-объектом без markdown и пояснений: "
    '{"category": "spam|insult|adult|minor|fraud|other", '
    '"severity": "low|medium|high", '
    '"summary": "2-3 фразы по-русски: суть нарушения и что сделать модератору"}'
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("report-ai")

PSQL = [
    "docker",
    "exec",
    "anon-chat-bot-db-1",
    "psql",
    "-U",
    "anon",
    "-d",
    "anon_chat",
    "-v",
    "ON_ERROR_STOP=1",
    "-t",
    "-A",
    "-c",
]

_llama = None
_last_used = 0.0
_failures: dict = {}
_db_error_logged = False


def psql(sql, timeout=30):
    """Один запрос через psql в контейнере БД; None -- база не ответила."""
    try:
        proc = subprocess.run(
            PSQL + [sql], capture_output=True, text=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.error("psql недоступен: %s", exc)
        return None
    if proc.returncode != 0:
        log.error("psql: %s", proc.stderr.strip()[:300])
        return None
    return proc.stdout


def q(value):
    """SQL-литерал: одинарные кавычки удваиваем -- текст жалоб чужой."""
    return "'" + str(value).replace("'", "''") + "'"


def fetch_pending():
    """Жалобы без вердикта (не больше BATCH); None -- БД не ответила."""
    global _db_error_logged
    rows = psql(
        "SELECT row_to_json(t) FROM ("
        " SELECT r.id, r.reason, coalesce(r.message_content, '') AS content"
        " FROM reports r LEFT JOIN report_ai a ON a.report_id = r.id"
        f" WHERE r.status='pending' AND a.report_id IS NULL"
        f" ORDER BY r.id LIMIT {BATCH}) t"
    )
    if rows is None:
        if not _db_error_logged:
            log.error(
                "БД не отвечает или report_ai ещё не создана "
                "(ждём деплой и рестарт бота)"
            )
            _db_error_logged = True
        return None
    if _db_error_logged:
        log.info("БД снова отвечает")
        _db_error_logged = False
    out = []
    for line in rows.splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("не-JSON строка из psql: %s", line[:120])
    return out


def ensure_llama():
    """Поднять llama-server, если он мёртв; True -- можно слать запрос."""
    global _llama, _last_used
    if _llama is not None and _llama.poll() is None:
        _last_used = time.time()
        return True

    def _oom_last():
        # Первым под нож при нехватке RAM: бот и веб важнее классификатора.
        try:
            with open("/proc/self/oom_score_adj", "w") as fh:
                fh.write("1000")
        except OSError as exc:
            log.warning("oom_score_adj не выставлен: %s", exc)

    cmd = [
        LLAMA,
        "-m",
        MODEL,
        "--port",
        PORT,
        "--host",
        "127.0.0.1",
        "--ctx-size",
        "2048",
        "--parallel",
        "1",
        "--threads",
        "1",
        "--jinja",
    ]
    handle = open(LLAMA_LOG, "a")
    _llama = subprocess.Popen(
        cmd, stdout=handle, stderr=subprocess.STDOUT, preexec_fn=_oom_last
    )
    handle.close()  # у child свой дубликат дескриптора
    log.info("llama-server стартует (pid %s)", _llama.pid)

    deadline = time.time() + HEALTH_TIMEOUT
    while time.time() < deadline:
        if _llama.poll() is not None:
            log.error("llama-server умер при старте, см. %s", LLAMA_LOG)
            return False
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=3) as resp:
                if resp.status == 200:
                    _last_used = time.time()
                    log.info("llama-server готов")
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    log.error("llama-server не поднялся за %d с, см. %s", HEALTH_TIMEOUT, LLAMA_LOG)
    return False


def maybe_stop():
    """Погасить модель после простоя: RAM возвращается системе."""
    global _llama
    if _llama is None or _llama.poll() is not None:
        return
    if time.time() - _last_used <= IDLE_SECONDS:
        return
    log.info("простой %d с -- гасим модель", IDLE_SECONDS)
    _llama.terminate()
    try:
        _llama.wait(timeout=20)
    except subprocess.TimeoutExpired:
        _llama.kill()
    _llama = None


def _extract_json(text):
    """Первый объект {...} из ответа: модель любит ограждения и хвосты."""
    cleaned = re.sub(r"```(?:json)?", "", text or "")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


def ask_model(reason, content):
    """Один проход инференса; dict-вердикт или None (мусор/сбой)."""
    global _last_used
    body = json.dumps(
        {
            "model": "qwen3-0.6b",
            "max_tokens": 350,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Причина жалобы: {reason}\n\nДиалог:\n"
                    f"{content[:CONTENT_LIMIT]}\n\nJSON:",
                },
            ],
            "chat_template_kwargs": {"enable_thinking": False},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.error("инференс не удался: %s", exc)
        return None
    _last_used = time.time()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        log.error("неожиданный формат ответа: %s", str(data)[:300])
        return None
    payload = _extract_json(text)
    if not isinstance(payload, dict):
        return None
    category = str(payload.get("category", "")).lower()
    severity = str(payload.get("severity", "")).lower()
    summary = re.sub(r"\s+", " ", str(payload.get("summary", ""))).strip()
    if category not in CATEGORIES or severity not in SEVERITIES or not summary:
        log.warning("вердикт вне схемы: %s", str(payload)[:300])
        return None
    return {"category": category, "severity": severity, "summary": summary[:500]}


def store(report_id, category, severity, summary, model):
    """Вердикт в report_ai; ON CONFLICT держит повторный проход идемпотентным."""
    sql = (
        "INSERT INTO report_ai (report_id, category, severity, summary,"
        " model) VALUES"
        f"({int(report_id)}, {q(category)}, {q(severity)},"
        f" {q(summary)}, {q(model)})"
        " ON CONFLICT (report_id) DO NOTHING"
    )
    if psql(sql) is None:
        log.error("вердикт по жалобе #%s не записался", report_id)
    else:
        log.info("жалоба #%s -> %s/%s: %s", report_id, category, severity, summary)


def classify(report):
    rid = int(report.get("id") or 0)
    verdict = ask_model(report.get("reason", ""), report.get("content", ""))
    if verdict:
        store(
            rid,
            verdict["category"],
            verdict["severity"],
            verdict["summary"],
            "qwen3-0.6b-q4_k_m",
        )
        _failures.pop(rid, None)
        return
    _failures[rid] = _failures.get(rid, 0) + 1
    log.warning("жалоба #%s: разбор не удался, попытка %s", rid, _failures[rid])
    if _failures[rid] >= MAX_ATTEMPTS:
        store(
            rid,
            "other",
            "unknown",
            "модель не смогла разобрать ответ за "
            f"{_failures[rid]} попытки -- открой текст жалобы вручную",
            "qwen3-0.6b-q4_k_m/parse-fail",
        )
        _failures.pop(rid, None)


def drain():
    """Пока есть жалобы без вердикта -- разбирать их пачками, пока модель жива."""
    while True:
        rows = fetch_pending()
        if not rows:
            return
        for report in rows:
            if not ensure_llama():
                log.error("модель не поднялась -- выходим до следующего цикла")
                return
            classify(report)


def main():
    log.info("вотчер жалоб запущен (модель: %s)", MODEL)

    def _stop(signum, frame):
        log.info("сигнал %s -- выходим", signum)
        if _llama is not None and _llama.poll() is None:
            _llama.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while True:
        maybe_stop()
        drain()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
