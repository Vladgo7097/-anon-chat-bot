"""Lifecycle regression tests for maintenance's one-task-per-job registry."""

import asyncio
import unittest
from unittest.mock import patch

from workers import maintenance


class FakeTask:
    def __init__(self):
        self.finished = False
        self.is_cancelled = False
        self.callback = None

    def done(self):
        return self.finished

    def cancelled(self):
        return self.is_cancelled

    def exception(self):
        if self.is_cancelled:
            raise asyncio.CancelledError()
        return None

    def add_done_callback(self, callback):
        self.callback = callback


class PeriodicTaskTests(unittest.TestCase):
    def setUp(self):
        self.previous = maintenance._PERIODIC.copy()
        maintenance._PERIODIC.clear()

    def tearDown(self):
        maintenance._PERIODIC.clear()
        maintenance._PERIODIC.update(self.previous)

    def test_stale_completion_does_not_remove_replacement(self):
        previous, replacement = FakeTask(), FakeTask()
        with patch.object(
            maintenance.asyncio,
            "create_task",
            side_effect=[previous, replacement],
        ):
            maintenance._spawn_periodic("sweep", lambda: None)
            previous.finished = True
            maintenance._spawn_periodic("sweep", lambda: None)

        previous.callback(previous)
        self.assertIs(maintenance._PERIODIC["sweep"], replacement)

    def test_cancelled_task_callback_is_safe_during_shutdown(self):
        task = FakeTask()
        with patch.object(
            maintenance.asyncio, "create_task", return_value=task
        ):
            maintenance._spawn_periodic("cleanup", lambda: None)

        task.finished = True
        task.is_cancelled = True
        task.callback(task)
        self.assertNotIn("cleanup", maintenance._PERIODIC)

