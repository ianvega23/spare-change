"""Local commands ask for missing secrets instead of needing shell-specific `export` lines."""
import pathlib
import tempfile
import unittest

from spare import askenv, setup


class FillTests(unittest.TestCase):
    def test_asks_only_for_missing_values_in_a_terminal(self):
        env = {"A": "set"}
        asked = []
        askenv.fill([("A", "a", True), ("B", "b", True), ("C", "c", False)], env, interactive=True,
                    ask_secret=lambda p: asked.append(p) or " secret ", ask=lambda p: asked.append(p) or "42")
        self.assertEqual(env, {"A": "set", "B": "secret", "C": "42"})
        self.assertEqual(len(asked), 2)
        self.assertIn("hidden", asked[0])

    def test_never_asks_without_a_terminal(self):
        env = {}
        askenv.fill([("B", "b", True)], env, interactive=False,
                    ask_secret=lambda p: self.fail("asked in CI"))
        self.assertEqual(env, {})

    def test_teller_files_are_used(self):
        with tempfile.TemporaryDirectory() as d:
            folder = pathlib.Path(d)
            (folder / "token.txt").write_text("tok\n", encoding="utf-8")
            (folder / "certificate.pem").write_text("x", encoding="utf-8")
            env = {"TELLER_KEY": "mine"}
            askenv.teller_defaults(folder, env)
            self.assertEqual(env["TELLER_ACCESS_TOKEN"], "tok")
            self.assertEqual(env["TELLER_CERT"], str(folder / "certificate.pem"))
            self.assertEqual(env["TELLER_KEY"], "mine")  # an explicit value wins


class SetupPathTests(unittest.TestCase):
    PEM = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n"

    def test_windows_copy_as_path_quotes_are_stripped(self):
        self.assertEqual(setup.clean_path('"C:\\Users\\ian\\cert.pem"'), pathlib.Path("C:\\Users\\ian\\cert.pem"))
        self.assertEqual(setup.clean_path("  'a b/c.pem' "), pathlib.Path("a b/c.pem"))

    def test_defaults_from_the_teller_folder(self):
        with tempfile.TemporaryDirectory() as d:
            base = pathlib.Path(d)
            (base / "teller").mkdir()
            (base / "teller" / "token.txt").write_text("tok-123\n", encoding="utf-8")
            (base / "teller" / "certificate.pem").write_text(self.PEM, encoding="utf-8")
            token = next(s for s in setup.BANK_SECRETS["teller"] if s.name == "TELLER_ACCESS_TOKEN")
            cert = next(s for s in setup.BANK_SECRETS["teller"] if s.name == "TELLER_CERT_PEM")
            self.assertEqual(setup.read_value(token, lambda p: self.fail("asked"), lambda p: "", base), "tok-123")
            self.assertEqual(setup.read_value(cert, lambda p: self.fail("asked"), lambda p: "", base), self.PEM)


class LegacyCodePageTest(unittest.TestCase):
    def test_output_survives_a_cp1252_pipe(self):
        """Windows pipes default to cp1252; `spare eval` prints characters it can't encode."""
        import os
        import subprocess
        import sys
        root = pathlib.Path(__file__).resolve().parents[1]
        r = subprocess.run([sys.executable, "-m", "spare", "eval"], cwd=root, capture_output=True,
                           env={**os.environ, "PYTHONIOENCODING": "cp1252"}, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[-500:])


if __name__ == "__main__":
    unittest.main()
