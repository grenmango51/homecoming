"""Exercise the loading predicate against Google's observed indicator markup.

Read the JS literal without importing the Playwright-dependent browser package.
"""
import ast
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node is needed for the DOM predicate regression")
class GoogleLoadingDOMTests(unittest.TestCase):
    def test_visible_decorative_loaders_and_hidden_finished_indicators(self):
        tree = ast.parse((ROOT / "src/browser/google.py").read_text(encoding="utf-8"))
        script = next(ast.literal_eval(node.value) for node in tree.body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "_RESULT_STATE_JS"
                              for target in node.targets))
        result = subprocess.run(
            [shutil.which("node"), str(ROOT / "tests/fixtures/google_loading_dom.cjs")],
            input=script, text=True, encoding="utf-8", capture_output=True, check=True,
        )
        cases = json.loads(result.stdout)
        for name in ("plane_after_hidden_first_bar", "cheapest_spinner", "nearby_airports", "skeleton"):
            with self.subTest(name=name):
                self.assertTrue(cases[name]["loading"])
                self.assertTrue(cases[name]["has_cards"])
        for name in ("finished_hidden_labels", "hidden_parent", "zero_sized_spinner"):
            with self.subTest(name=name):
                self.assertFalse(cases[name]["loading"])
                self.assertTrue(cases[name]["has_cards"])
        self.assertEqual(cases["plane_after_hidden_first_bar"]["visible_progressbars"], 1)
        self.assertEqual(cases["cheapest_spinner"]["visible_progressbars"], 1)
        self.assertNotEqual(cases["finished_hidden_labels"]["signature"], cases["cheaper_update"]["signature"])
        self.assertEqual(cases["finished_hidden_labels"]["tab_texts"], ["Cheapest €631"])
