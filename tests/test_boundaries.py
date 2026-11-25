"""Architecture rules from docs/PLAN.md section 2, enforced as tests."""
import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "spare" / "core"


def imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield 0, alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.level, node.module or ""


class Boundaries(unittest.TestCase):
    def core_files(self):
        files = sorted(CORE.glob("*.py"))
        self.assertTrue(files)
        return files

    def test_core_never_imports_brain_or_bot(self):
        for path in self.core_files():
            for level, module in imports(path):
                absolute = module if level == 0 else ("spare." + module if level == 2 else "spare.core." + module)
                self.assertFalse(absolute.startswith(("spare.brain", "spare.bot")), f"{path.name}: {module}")
                self.assertFalse(level >= 2 and module.split(".")[0] in ("brain", "bot"), f"{path.name}: {module}")
                self.assertLess(level, 3, f"{path.name} reaches outside the package")

    def test_core_is_stdlib_only(self):
        for path in self.core_files():
            for level, module in imports(path):
                if level:
                    self.assertEqual(level, 1, f"{path.name}: core imports only within core")
                    continue
                top = module.split(".")[0]
                self.assertTrue(top in sys.stdlib_module_names or top == "__future__",
                                f"{path.name} imports non-stdlib {module!r}")

    def test_no_floats_for_money_fields_in_ledger_events(self):
        source = (CORE / "invest.py").read_text(encoding="utf-8")
        self.assertNotIn("float(", source)


if __name__ == "__main__":
    unittest.main()
