import tempfile
import unittest
from pathlib import Path

from calbot.personality import (
    DEFAULT_PERSONALITY,
    MAX_PERSONALITY_CHARS,
    load_personality,
)


class PersonalityTests(unittest.TestCase):
    def test_missing_file_uses_safe_default(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.md"

            with self.assertLogs("assistant-bot", level="WARNING"):
                loaded = load_personality(missing)

        self.assertEqual(loaded, DEFAULT_PERSONALITY)

    def test_personality_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "PERSONALITY.md"
            path.write_text("x" * (MAX_PERSONALITY_CHARS + 100), encoding="utf-8")

            with self.assertLogs("assistant-bot", level="WARNING"):
                loaded = load_personality(path)

        self.assertEqual(len(loaded), MAX_PERSONALITY_CHARS)


if __name__ == "__main__":
    unittest.main()
