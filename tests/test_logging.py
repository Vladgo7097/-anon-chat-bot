"""Structured logging: the formatter must never crash a log call, and a
cancelled update must not be recorded as a successful one."""
import asyncio
import json
import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.logging import JsonFormatter, UpdateLoggingMiddleware


class JsonFormatterTests(unittest.TestCase):
    def record(self, exc_info=None):
        return logging.LogRecord("test", logging.INFO, __file__, 1, "msg", None, exc_info)

    def test_real_exception_info_reports_the_error_class(self):
        try:
            raise ValueError("boom")
        except ValueError:
            payload = json.loads(JsonFormatter().format(self.record(sys.exc_info())))
        self.assertEqual(payload["error_code"], "ValueError")

    def test_exc_info_true_outside_an_except_block_does_not_crash(self):
        # logging.exception()/exc_info=True resolves to (None, None, None) via
        # sys.exc_info() when there is no active exception -- still a truthy
        # tuple, so a naive `if record.exc_info:` mis-detects it as real.
        payload = json.loads(JsonFormatter().format(self.record((None, None, None))))
        self.assertNotIn("error_code", payload)

    def test_no_exc_info_is_unaffected(self):
        payload = json.loads(JsonFormatter().format(self.record(None)))
        self.assertNotIn("error_code", payload)


class UpdateLoggingMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def run_with(self, handler):
        middleware = UpdateLoggingMiddleware()
        records = []
        logger = logging.getLogger("updates")
        handler_log = logging.Handler()
        handler_log.emit = lambda record: records.append(record)
        logger.addHandler(handler_log)
        logger.setLevel(logging.INFO)
        try:
            await middleware(handler, SimpleNamespace(from_user=None), {})
        finally:
            logger.removeHandler(handler_log)
        return records[-1]

    async def test_success_is_logged_as_ok(self):
        record = await self.run_with(AsyncMock(return_value=None))
        self.assertEqual(record.result, "ok")

    async def test_cancellation_result_is_cancelled_not_ok(self):
        middleware = UpdateLoggingMiddleware()
        records = []
        logger = logging.getLogger("updates")
        handler_log = logging.Handler()
        handler_log.emit = lambda record: records.append(record)
        logger.addHandler(handler_log)
        logger.setLevel(logging.INFO)

        async def cancelled(event, data):
            raise asyncio.CancelledError()

        try:
            with self.assertRaises(asyncio.CancelledError):
                await middleware(cancelled, SimpleNamespace(from_user=None), {})
        finally:
            logger.removeHandler(handler_log)
        self.assertEqual(records[-1].result, "cancelled")
        self.assertNotEqual(records[-1].result, "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
