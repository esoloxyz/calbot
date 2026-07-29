import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ContainerDependencyTests(unittest.TestCase):
    def test_base_images_are_digest_pinned(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        from_lines = re.findall(r"(?m)^FROM\s+(.+)$", dockerfile)

        self.assertEqual(len(from_lines), 2)
        self.assertTrue(all("@sha256:" in line for line in from_lines))

    def test_runtime_is_unprivileged_and_read_only(self):
        dockerfile = (ROOT / "Dockerfile").read_text()

        self.assertIn("AS builder", dockerfile)
        self.assertIn("ENV HOME=/home/calbot", dockerfile)
        self.assertIn("install -d -o root -g root -m 0755 /app", dockerfile)
        self.assertIn("chmod -R a-w /app/calbot", dockerfile)
        self.assertRegex(dockerfile, r"(?m)^USER calbot:calbot$")

    def test_dependencies_use_hashed_binary_only_install(self):
        dockerfile = (ROOT / "Dockerfile").read_text()

        self.assertIn("--only-binary=:all:", dockerfile)
        self.assertIn("--require-hashes", dockerfile)

    def test_runtime_copy_is_explicit(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        dockerignore = (ROOT / ".dockerignore").read_text()

        self.assertNotRegex(dockerfile, r"(?m)^COPY\s+\.\s+\.$")
        self.assertIn(".git", dockerignore)
        self.assertIn(".env", dockerignore)
        self.assertIn("tests", dockerignore)

    def test_container_has_no_unrelated_runtime(self):
        dockerfile = (ROOT / "Dockerfile").read_text().casefold()

        self.assertNotIn("tempo", dockerfile)
        self.assertNotIn("doordash", dockerfile)
        self.assertNotIn("sqlite3", dockerfile)

    def test_ci_is_read_only_and_actions_are_sha_pinned(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        action_refs = re.findall(r"(?m)^\s*uses:\s*([^\s#]+)", workflow)

        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertTrue(action_refs)
        self.assertTrue(
            all(re.search(r"@[0-9a-f]{40}$", ref) for ref in action_refs),
            action_refs,
        )
        self.assertNotIn("pull_request_target", workflow)
        self.assertIn("persist-credentials: false", workflow)

    def test_ci_checks_tests_style_shell_and_image(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

        self.assertIn("uv pip compile", workflow)
        self.assertIn("--require-hashes", workflow)
        self.assertIn("unittest discover", workflow)
        self.assertIn("compileall", workflow)
        self.assertIn("bash -n start.sh", workflow)
        self.assertIn("docker build", workflow)
        self.assertIn("ruff check .", workflow)
        self.assertIn("ruff format --check .", workflow)
        self.assertIn("import calbot.runtime, calbot.telegram_app", workflow)


if __name__ == "__main__":
    unittest.main()
