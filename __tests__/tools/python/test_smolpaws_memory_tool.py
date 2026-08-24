import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

from openhands.sdk.tool.registry import list_usable_tools

from smolpaws_memory_store import SmolPawsMemoryStore
from smolpaws_memory_tool import (
    SmolPawsMemoryAction,
    SmolPawsMemoryExecutor,
)


class SmolPawsMemoryRegistrationTests(unittest.TestCase):
    def test_module_registers_server_executed_tool(self):
        self.assertIn("smolpaws_memory", list_usable_tools())


class SmolPawsMemoryExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = SmolPawsMemoryStore(
            root=Path(self.temporary_directory.name) / "memory"
        )
        self.executor = SmolPawsMemoryExecutor(store=self.store)
        self.conversation = SimpleNamespace(
            state=SimpleNamespace(tags={"smolpaws": "insider"})
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def execute(self, **kwargs):
        observation = self.executor(
            SmolPawsMemoryAction(**kwargs), conversation=self.conversation
        )
        self.assertFalse(observation.is_error)
        return observation.content[0].text

    def test_read_replace_and_conflict_return_structured_json(self):
        read_text = self.execute(operation="read_durable")
        revision = self._json(read_text)["revision"]

        replaced_text = self.execute(
            operation="replace_durable",
            content="# SmolPaws Memory\n\n- remembered\n",
            expected_revision=revision,
        )
        replaced = self._json(replaced_text)
        self.assertEqual(replaced["status"], "updated")
        self.assertNotIn("content", replaced)

        conflict = self.executor(
            SmolPawsMemoryAction(
                operation="replace_durable",
                content="stale",
                expected_revision=revision,
            ),
            conversation=self.conversation,
        )
        self.assertTrue(conflict.is_error)
        payload = self._json(conflict.content[0].text)
        self.assertEqual(payload["error"], "revision_conflict")
        self.assertEqual(payload["current_revision"], replaced["revision"])

    def test_append_list_and_read_daily(self):
        appended = self._json(self.execute(operation="append_today", content="note"))
        self.assertNotIn("content", appended)
        listed = self._json(self.execute(operation="list_dailies"))
        daily = self._json(self.execute(operation="read_daily", date=appended["date"]))

        self.assertEqual(listed["dailies"][0]["date"], appended["date"])
        self.assertIn("note", daily["content"])

    def test_rejects_parameters_not_used_by_operation(self):
        observation = self.executor(
            SmolPawsMemoryAction(operation="read_durable", content="unused"),
            conversation=self.conversation,
        )
        self.assertTrue(observation.is_error)
        self.assertEqual(
            self._json(observation.content[0].text)["error"], "invalid_request"
        )

    def test_rejects_calls_without_the_insider_tag(self):
        for tags in ({}, {"smolpaws": "whatsapp"}):
            with self.subTest(tags=tags):
                observation = self.executor(
                    SmolPawsMemoryAction(operation="read_durable"),
                    conversation=SimpleNamespace(state=SimpleNamespace(tags=tags)),
                )
                self.assertTrue(observation.is_error)
                self.assertEqual(
                    self._json(observation.content[0].text)["error"], "forbidden"
                )

        missing_conversation = self.executor(
            SmolPawsMemoryAction(operation="read_durable")
        )
        self.assertTrue(missing_conversation.is_error)
        self.assertEqual(
            self._json(missing_conversation.content[0].text)["error"], "forbidden"
        )
        self.assertFalse(self.store.root.exists())

    @staticmethod
    def _json(value):
        import json

        return json.loads(value)


if __name__ == "__main__":
    unittest.main()
