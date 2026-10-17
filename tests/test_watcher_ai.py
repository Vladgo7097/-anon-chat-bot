"""Чистые функции вотчера жалоб (deploy/ai/watcher.py): без модели и БД.

Всё, что стоит между ответом 0.6B и строкой в report_ai: разбор JSON,
алиасы русских лейблов, пол severity и гейт сводки. Ни сети, ни docker,
ни llama-server здесь не нужно -- только импорт файла вотчера.
"""
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

WATCHER_PATH = (
    Path(__file__).resolve().parent.parent / "deploy" / "ai" / "watcher.py"
)
_spec = importlib.util.spec_from_file_location("report_ai_watcher", WATCHER_PATH)
watcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watcher)


def raw(category, severity, summary):
    """Сырой ответ модели, как его прислал бы llama-server."""
    return json.dumps(
        {"category": category, "severity": severity, "summary": summary},
        ensure_ascii=False,
    )


class ParseVerdictTest(unittest.TestCase):
    def test_clean_json(self):
        verdict = watcher.parse_verdict(raw("spam", "medium", "заходи в @signals"))
        self.assertEqual(
            verdict,
            {
                "category": "spam",
                "severity": "medium",
                "summary": "заходи в @signals",
            },
        )

    def test_fenced_and_prose_wrapped_json(self):
        fenced = "```json\n" + raw("insult", "low", "дурак и лох") + "\n```"
        prose = "Вот вердикт: " + raw("insult", "low", "дурак и лох") + " Конец"
        self.assertEqual(watcher.parse_verdict(fenced)["category"], "insult")
        self.assertEqual(watcher.parse_verdict(prose)["summary"], "дурак и лох")

    def test_garbage_is_none(self):
        self.assertIsNone(watcher.parse_verdict("извините, не могу"))
        self.assertIsNone(watcher.parse_verdict(""))

    def test_unknown_category_is_none(self):
        self.assertIsNone(watcher.parse_verdict(raw("nonsense", "low", "текст")))

    def test_empty_summary_is_none(self):
        self.assertIsNone(watcher.parse_verdict(raw("spam", "low", "   ")))

    def test_category_alias_and_floor_together(self):
        verdict = watcher.parse_verdict(raw("реклама", "низкая", "канал про заработок"))
        self.assertEqual(verdict["category"], "spam")
        self.assertEqual(verdict["severity"], "medium")  # алиас + пол

    def test_floor_lifts_low_and_medium(self):
        cases = [
            ("minor", "low", "high"),
            ("fraud", "low", "high"),
            ("fraud", "medium", "high"),
            ("adult", "low", "medium"),
            ("spam", "low", "medium"),
        ]
        for category, severity, expected in cases:
            verdict = watcher.parse_verdict(raw(category, severity, "сводка текста"))
            self.assertEqual(verdict["severity"], expected, category)

    def test_model_may_raise_above_floor(self):
        self.assertEqual(
            watcher.parse_verdict(raw("insult", "high", "сводка"))["severity"],
            "high",
        )
        self.assertEqual(
            watcher.parse_verdict(raw("other", "medium", "сводка"))["severity"],
            "medium",
        )

    def test_reason_prefix_stripped(self):
        verdict = watcher.parse_verdict(
            raw("spam", "medium", "Причина жалобы: Спам/реклама")
        )
        self.assertEqual(verdict["summary"], "Спам/реклама")

    def test_summary_whitespace_and_trailing_dot(self):
        verdict = watcher.parse_verdict(raw("spam", "medium", "  Спам.  Ну да.  "))
        self.assertEqual(verdict["summary"], "Спам. Ну да")

    def test_summary_truncated_to_500(self):
        verdict = watcher.parse_verdict(raw("spam", "medium", "а" * 600))
        self.assertEqual(len(verdict["summary"]), 500)


class FiltersTest(unittest.TestCase):
    def test_echo_of_instruction_rejected(self):
        self.assertFalse(
            watcher.passes_filters(
                "Контент 18+ -- нежелательный контент; предупредить или отклонить"
            )
        )

    def test_degenerate_repetition_rejected(self):
        self.assertFalse(watcher.passes_filters("звучит звучит звучит и всё"))

    def test_two_repeats_pass(self):
        self.assertTrue(watcher.passes_filters("звучит звучит, но дальше по делу"))

    def test_reason_label_passes(self):
        # На пустых репликах метка категории -- лучшее, что есть (осознанно).
        self.assertTrue(watcher.passes_filters("Контент 18+"))

    def test_quote_from_dialog_passes(self):
        self.assertTrue(watcher.passes_filters("скинь 500 на карту, верну вдвойне"))


class SchemaTest(unittest.TestCase):
    def test_schema_enums_match_constants(self):
        props = watcher.VERDICT_SCHEMA["properties"]
        self.assertEqual(props["category"]["enum"], sorted(watcher.CATEGORIES))
        self.assertEqual(props["severity"]["enum"], ["low", "medium", "high"])

    def test_floor_covers_all_categories(self):
        self.assertEqual(set(watcher.SEVERITY_FLOOR), set(watcher.CATEGORIES))

    def test_every_verdict_respects_floor(self):
        for category in sorted(watcher.CATEGORIES):
            for severity in ("low", "medium", "high"):
                verdict = watcher.parse_verdict(
                    raw(category, severity, "сводка текста")
                )
                floor = watcher.SEVERITY_FLOOR[category]
                self.assertGreaterEqual(
                    watcher.SEVERITY_RANK[verdict["severity"]],
                    watcher.SEVERITY_RANK[floor],
                    f"{category}/{severity}",
                )


class MemoryGuardTest(unittest.TestCase):
    def test_mem_available_reads_positive_int(self):
        avail = watcher.mem_available_mb()
        self.assertIsInstance(avail, int)
        self.assertGreater(avail, 0)

    def test_ensure_llama_skips_start_when_memory_is_low(self):
        self.assertIsNone(watcher._llama)
        with patch.object(watcher, "MEM_MIN_MB", 10**9):
            self.assertFalse(watcher.ensure_llama())
        self.assertIsNone(watcher._llama)  # процесс даже не стартовал


class AlertLogicTest(unittest.TestCase):
    def test_no_alert_below_threshold(self):
        now = 1_000_000.0
        self.assertFalse(watcher.alert_needed(100, 0.0, 0.0, now))
        self.assertFalse(watcher.alert_needed(None, 0.0, 0.0, now))

    def test_alert_when_stuck_and_quiet(self):
        now = 1_000_000.0
        age = watcher.STUCK_SECONDS + 60
        self.assertTrue(watcher.alert_needed(age, 0.0, 0.0, now))

    def test_repeat_window_suppresses(self):
        now = 1_000_000.0
        age = watcher.STUCK_SECONDS + 60
        self.assertFalse(watcher.alert_needed(age, now - 100.0, now - 100.0, now))

    def test_failed_send_retries_after_backoff(self):
        now = 1_000_000.0
        age = watcher.STUCK_SECONDS + 60
        # прошлая попытка только что не удалась -- ждём бэкофф
        self.assertFalse(watcher.alert_needed(age, 0.0, now - 1.0, now))
        # бэкофф истёк, алертов давно не было -- пора
        self.assertTrue(
            watcher.alert_needed(age, 0.0, now - watcher.ALERT_RETRY_SECONDS, now)
        )

    def test_alert_text_mentions_minutes_and_service(self):
        text = watcher.build_alert_text(47 * 60)
        self.assertIn("47 мин", text)
        self.assertIn("llm-report-watcher", text)


class WatchdogTest(unittest.TestCase):
    def test_sd_notify_survives_bad_socket(self):
        with patch.dict(os.environ, {"NOTIFY_SOCKET": "/nonexistent/watchdog.sock"}):
            watcher._sd_notify("READY=1")  # не должно бросить

    def test_sd_notify_without_socket_is_noop(self):
        env = {k: v for k, v in os.environ.items() if k != "NOTIFY_SOCKET"}
        with patch.dict(os.environ, env, clear=True):
            watcher._sd_notify("WATCHDOG=1")  # тихо ничего


if __name__ == "__main__":
    unittest.main()
