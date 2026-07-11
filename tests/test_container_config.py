import pathlib
import unittest


class ContainerDependencyTests(unittest.TestCase):
    def test_tempo_payment_runtime_has_sqlite(self):
        dockerfile = pathlib.Path("Dockerfile").read_text()

        self.assertRegex(dockerfile, r"(?m)^\s*sqlite3\s*\\$")


if __name__ == "__main__":
    unittest.main()
