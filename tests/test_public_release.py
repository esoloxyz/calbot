import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class PublicReleaseTests(unittest.TestCase):
    def test_public_project_files_exist(self):
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((ROOT / "SECURITY.md").is_file())

    def test_legacy_wallet_secret_is_not_supported_or_documented(self):
        files = [
            ROOT / ".env.example",
            ROOT / "README.md",
            ROOT / "start.sh",
            ROOT / "tempo_client.py",
        ]
        combined = "\n".join(path.read_text() for path in files)

        self.assertNotIn("TEMPO_KEYS_TOML_B64", combined)
        self.assertNotIn("keys.toml", combined)

    def test_examples_do_not_contain_personalized_relationship_details(self):
        combined = (ROOT / "SETUP.md").read_text() + (ROOT / ".env.example").read_text()

        self.assertIsNone(
            re.search(
                r"Ezra|Maya|girlfriend|for you two|private GitHub repo", combined, re.I
            )
        )

    def test_example_telegram_token_is_blank(self):
        env_example = (ROOT / ".env.example").read_text()

        self.assertRegex(env_example, r"(?m)^TELEGRAM_BOT_TOKEN=\s*$")

    def test_production_dependencies_use_hashed_lock(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        lockfile = (ROOT / "requirements.lock").read_text()

        self.assertIn("--require-hashes -r requirements.lock", dockerfile)
        self.assertIn("--hash=sha256:", lockfile)

    def test_container_inputs_are_immutable_and_verified(self):
        dockerfile = (ROOT / "Dockerfile").read_text()

        self.assertRegex(
            dockerfile,
            r"(?m)^FROM ubuntu:24\.04@sha256:[0-9a-f]{64}$",
        )
        self.assertIn("TEMPOUP_COMMIT=", dockerfile)
        self.assertIn("TEMPOUP_SHA256=", dockerfile)
        self.assertIn("sha256sum -c -", dockerfile)
        self.assertIn("TEMPO_VERSION=v", dockerfile)


if __name__ == "__main__":
    unittest.main()
