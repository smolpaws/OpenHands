import os
import stat
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

from smolpaws_memory_store import (
    MAX_DAILY_ENTRY_BYTES,
    MAX_DURABLE_BYTES,
    MemoryConflictError,
    SmolPawsMemoryStore,
    memory_root,
)


class MemoryRootTests(unittest.TestCase):
    def test_uses_openhands_persistence_dir_when_set(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"OH_PERSISTENCE_DIR": directory}):
                self.assertEqual(memory_root(), Path(directory) / "smolpaws" / "memory")

    def test_falls_back_to_openhands_under_home(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("pathlib.Path.home", return_value=Path(directory)),
            ):
                self.assertEqual(
                    memory_root(),
                    Path(directory) / ".openhands" / "smolpaws" / "memory",
                )


class SmolPawsMemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "memory"
        self.store = SmolPawsMemoryStore(root=self.root)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_read_durable_initializes_index_and_returns_revision(self):
        memory = self.store.read_durable()

        self.assertEqual(memory.content, "# SmolPaws Memory\n")
        self.assertRegex(memory.revision, r"^[0-9a-f]{64}$")
        memory_path = self.root / "MEMORY.md"
        self.assertEqual(memory_path.read_text(), memory.content)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(memory_path.stat().st_mode), 0o600)

    def test_replace_durable_requires_current_revision(self):
        original = self.store.read_durable()
        updated = self.store.replace_durable(
            "# SmolPaws Memory\n\n- prefers concise answers\n",
            expected_revision=original.revision,
        )

        self.assertIn("prefers concise answers", updated.content)
        self.assertNotEqual(updated.revision, original.revision)
        with self.assertRaises(MemoryConflictError) as error:
            self.store.replace_durable(
                "stale writer", expected_revision=original.revision
            )
        self.assertEqual(error.exception.current_revision, updated.revision)
        self.assertEqual(self.store.read_durable(), updated)

    def test_replace_durable_is_size_bounded(self):
        original = self.store.read_durable()
        with self.assertRaisesRegex(ValueError, "durable memory exceeds"):
            self.store.replace_durable(
                "x" * (MAX_DURABLE_BYTES + 1),
                expected_revision=original.revision,
            )

    def test_read_durable_rejects_oversized_external_content(self):
        self.store.read_durable()
        (self.root / "MEMORY.md").write_bytes(b"x" * (MAX_DURABLE_BYTES + 1))

        with self.assertRaisesRegex(ValueError, "memory file exceeds"):
            self.store.read_durable()

    def test_read_durable_rejects_symbolic_links(self):
        outside = self.root.parent / "outside.md"
        outside.write_text("outside")
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            (self.root / "MEMORY.md").symlink_to(outside)
        except (NotImplementedError, OSError):
            self.skipTest("symbolic links are unavailable")

        with self.assertRaisesRegex(ValueError, "must not be symbolic links"):
            self.store.read_durable()

    def test_append_today_is_locked_across_concurrent_writers(self):
        entries = [f"entry-{index}" for index in range(24)]
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(self.store.append_today, entries))

        self.assertTrue(all(result.date == results[0].date for result in results))
        daily = self.store.read_daily(results[0].date)
        self.assertIsNotNone(daily)
        assert daily is not None
        self.assertTrue(daily.content.startswith(f"# {daily.date}\n"))
        daily_lines = daily.content.splitlines()
        for entry in entries:
            self.assertEqual(daily_lines.count(entry), 1)

    def test_append_today_rejects_empty_and_oversized_entries(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            self.store.append_today("  \n")
        with self.assertRaisesRegex(ValueError, "daily entry exceeds"):
            self.store.append_today("x" * (MAX_DAILY_ENTRY_BYTES + 1))

    def test_append_today_does_not_overwrite_invalid_utf8(self):
        document = self.store.append_today("valid")
        path = self.root / f"{document.date}.md"
        path.write_bytes(b"invalid: \xff")

        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            self.store.append_today("new entry")
        self.assertEqual(path.read_bytes(), b"invalid: \xff")

    def test_read_and_list_dailies_accept_dates_not_paths(self):
        first = self.store.append_today("today")
        self.assertEqual(self.store.list_dailies()[0].date, first.date)
        self.assertIsNone(self.store.read_daily("2020-01-01"))

        for value in ["../MEMORY.md", "2026-2-01", "2026-02-30", "MEMORY.md"]:
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "valid YYYY-MM-DD"),
            ):
                self.store.read_daily(value)


if __name__ == "__main__":
    unittest.main()
