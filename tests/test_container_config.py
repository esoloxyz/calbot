import os
import pathlib
import re
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ContainerDependencyTests(unittest.TestCase):
    def test_tempo_payment_runtime_has_sqlite(self):
        dockerfile = (ROOT / "Dockerfile").read_text()

        self.assertRegex(dockerfile, r"(?m)^\s*sqlite3\s*\\$")

    def test_base_images_are_digest_pinned(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        from_lines = re.findall(r"(?m)^FROM\s+(.+)$", dockerfile)

        self.assertGreaterEqual(len(from_lines), 2)
        self.assertTrue(all("@sha256:" in line for line in from_lines))

    def test_runtime_is_unprivileged_and_uses_a_writable_tempo_home(self):
        dockerfile = (ROOT / "Dockerfile").read_text()

        self.assertIn("AS builder", dockerfile)
        self.assertIn("ENV HOME=/home/calbot", dockerfile)
        self.assertIn("ENV TEMPO_BIN=/opt/tempo/bin/tempo", dockerfile)
        self.assertIn("ENV TEMPO_HOME=/opt/tempo", dockerfile)
        self.assertIn(
            "install -d -o root -g root -m 0755 /opt/tempo/bin",
            dockerfile,
        )
        self.assertIn("--chown=root:root --chmod=0555", dockerfile)
        self.assertIn("install -d -o root -g root -m 0755 /app", dockerfile)
        self.assertRegex(dockerfile, r"(?m)^USER calbot:calbot$")

    def test_dependency_and_tempo_installs_are_verified(self):
        dockerfile = (ROOT / "Dockerfile").read_text()

        self.assertIn("--only-binary=:all:", dockerfile)
        self.assertIn("--require-hashes", dockerfile)
        self.assertIn("sha256sum -c -", dockerfile)
        self.assertIn('tempoup --install "$TEMPO_VERSION"', dockerfile)
        self.assertIn('tempo add wallet "$TEMPO_WALLET_VERSION"', dockerfile)
        self.assertIn('tempo add request "$TEMPO_REQUEST_VERSION"', dockerfile)
        self.assertIn("/opt/tempo/bin/tempo-wallet", dockerfile)
        self.assertIn("/opt/tempo/bin/tempo-request", dockerfile)

    def test_tempo_commands_ignore_builder_telemetry_endpoints(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        stages = dockerfile.split("\nFROM ")

        self.assertEqual(len(stages), 2)
        for stage in stages:
            self.assertIn("ARG OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", stage)
            self.assertIn("unset OTEL_EXPORTER_OTLP_ENDPOINT", stage)
            self.assertIn("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", stage)

    def test_startup_exports_the_resolved_tempo_binary_path(self):
        startup = (ROOT / "start.sh").read_text()

        self.assertIn("export TEMPO_HOME TEMPO_BIN", startup)

    def test_startup_passes_a_computed_tempo_binary_to_python(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = pathlib.Path(directory)
            tempo_home = temporary / "tempo-home"
            tempo_bin = tempo_home / "bin" / "tempo"
            tempo_bin.parent.mkdir(parents=True)
            tempo_bin.write_text("#!/bin/sh\nprintf 'tempo test\\n'\n")
            tempo_bin.chmod(0o700)

            shim_dir = temporary / "shims"
            shim_dir.mkdir()
            capture = temporary / "captured-tempo-bin"
            python_shim = shim_dir / "python"
            python_shim.write_text(
                '#!/bin/sh\nprintf \'%s\' "$TEMPO_BIN" > "$CAPTURE_PATH"\n'
            )
            python_shim.chmod(0o700)

            environment = dict(os.environ)
            environment.pop("TEMPO_BIN", None)
            environment.update(
                {
                    "TEMPO_HOME": str(tempo_home),
                    "TEMPO_WALLET_STORE_B64": "test-only",
                    "CAPTURE_PATH": str(capture),
                    "PATH": f"{shim_dir}:{environment['PATH']}",
                }
            )
            subprocess.run(
                ["bash", str(ROOT / "start.sh")],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(capture.read_text(), str(tempo_bin))

    def test_runtime_copy_is_explicit(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        dockerignore = (ROOT / ".dockerignore").read_text()

        self.assertNotRegex(dockerfile, r"(?m)^COPY\s+\.\s+\.$")
        self.assertIn(".git", dockerignore)
        self.assertIn(".env", dockerignore)
        self.assertIn("tests", dockerignore)

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

    def test_ci_validates_lock_tests_syntax_and_image(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

        self.assertIn("uv pip compile", workflow)
        self.assertIn("--constraint", workflow)
        self.assertIn("requirements.current.normalized", workflow)
        self.assertIn("requirements.candidate.normalized", workflow)
        self.assertIn("diff -u", workflow)
        self.assertIn("--require-hashes", workflow)
        self.assertIn("unittest discover", workflow)
        self.assertIn("compileall", workflow)
        self.assertIn("bash -n start.sh", workflow)
        self.assertIn("docker build", workflow)
        self.assertIn("ruff check .", workflow)
        self.assertIn("ruff format --check .", workflow)
        self.assertIn("osv-scanner", workflow)
        self.assertIn("import calbot.runtime, calbot.telegram_app", workflow)
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("calbot/tempo/process_guard.py", dockerfile)
        self.assertIn("MAX_TEMPO_REQUEST_DATA_MEMORY_BYTES", dockerfile)
        self.assertNotIn("402653184", dockerfile)

    def test_documentation_does_not_pipe_remote_code_to_a_shell(self):
        documentation = "\n".join(
            (ROOT / filename).read_text() for filename in ("README.md", "SETUP.md")
        )

        self.assertNotRegex(documentation, r"curl[^\n]*\|\s*(?:ba)?sh")


if __name__ == "__main__":
    unittest.main()
