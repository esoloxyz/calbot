import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class PublicReleaseTests(unittest.TestCase):
    def test_project_files_exist(self):
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((ROOT / "SECURITY.md").is_file())
        self.assertTrue((ROOT / "SETUP.md").is_file())

    def test_example_secrets_are_blank(self):
        env_example = (ROOT / ".env.example").read_text()

        self.assertRegex(env_example, r"(?m)^TELEGRAM_BOT_TOKEN=\s*$")
        self.assertRegex(env_example, r"(?m)^ANTHROPIC_API_KEY=\s*$")
        self.assertRegex(env_example, r"(?m)^GOOGLE_SERVICE_ACCOUNT_JSON=\s*$")

    def test_removed_integrations_are_not_configurable(self):
        combined = "\n".join(
            (ROOT / filename).read_text()
            for filename in (
                ".env.example",
                "README.md",
                "SETUP.md",
                "start.sh",
                "requirements.txt",
            )
        ).casefold()

        self.assertNotIn("tempo_", combined)
        self.assertNotIn("doordash_", combined)
        self.assertNotIn("bot_mode", combined)

    def test_production_dependencies_use_hashed_lock(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        lockfile = (ROOT / "requirements.lock").read_text()

        self.assertIn("--require-hashes", dockerfile)
        self.assertIn("--hash=sha256:", lockfile)

    def test_documentation_does_not_pipe_remote_code_to_shell(self):
        documentation = "\n".join(
            (ROOT / filename).read_text() for filename in ("README.md", "SETUP.md")
        )

        self.assertNotRegex(documentation, r"curl[^\n]*\|\s*(?:ba)?sh")


if __name__ == "__main__":
    unittest.main()
