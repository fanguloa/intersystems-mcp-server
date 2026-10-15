"""Distribution contract: one version everywhere, and a wheel that carries every resource.

tools/smoke_install.py checks a built wheel end to end; these tests keep the declarations it
depends on honest without building anything.
"""

import fnmatch
import json
import re
import tomllib
import unittest
from pathlib import Path

from isc_ai import __version__

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


class VersionTests(unittest.TestCase):
    def test_the_package_version_is_the_only_version_source(self):
        self.assertIn("version", PYPROJECT["project"]["dynamic"])
        self.assertNotIn("version", PYPROJECT["project"])
        self.assertEqual(PYPROJECT["tool"]["setuptools"]["dynamic"]["version"], {"attr": "isc_ai.__version__"})

    def test_the_installers_default_to_the_release_tag_of_this_version(self):
        for name in ("install.ps1", "install.sh"):
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(installer=name):
                self.assertRegex(text, rf'ISC_AI_REF\s*=\s*"v{re.escape(__version__)}"')

    def test_the_release_pipeline_refuses_a_tag_that_is_not_this_version(self):
        text = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
        self.assertIn("CI_COMMIT_TAG", text)
        self.assertIn("isc_ai.__version__", text)
        self.assertIn("tools/smoke_install.py", text)


class AuthorTests(unittest.TestCase):
    def test_the_package_metadata_names_the_maintainer(self):
        for key in ("authors", "maintainers"):
            self.assertIn({"name": "Felipe Angulo", "email": "felipe.angulo@intersystems.com"},
                          PYPROJECT["project"][key])

    def test_the_readme_names_the_maintainer(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for value in ("Felipe Angulo", "Technical Specialist", "felipe.angulo@intersystems.com"):
            self.assertIn(value, text)


class PackageDataTests(unittest.TestCase):
    def setUp(self):
        self.globs = PYPROJECT["tool"]["setuptools"]["package-data"]["isc_ai"]

    def covered(self, relative):
        return any(fnmatch.fnmatch(relative, pattern) for pattern in self.globs)

    def test_every_harness_file_is_matched_by_a_package_data_glob(self):
        manifest = json.loads((ROOT / "isc_ai" / "resources" / "harness" / "manifest.json").read_text(encoding="utf-8"))
        uncovered = [entry["source"] for entry in manifest["files"]
                     if not self.covered("resources/harness/" + entry["source"])]
        self.assertEqual(uncovered, [])

    def test_every_packaged_resource_file_is_matched(self):
        package = ROOT / "isc_ai"
        uncovered = []
        for folder in ("resources", "logigen/templates"):
            for path in (package / folder).rglob("*"):
                relative = path.relative_to(package).as_posix()
                if path.is_file() and "__pycache__" not in relative and not relative.endswith(".py"):
                    if not self.covered(relative):
                        uncovered.append(relative)
        self.assertEqual(uncovered, [])

    def test_every_python_package_is_declared(self):
        declared = set(PYPROJECT["tool"]["setuptools"]["packages"])
        found = {path.parent.relative_to(ROOT).as_posix().replace("/", ".")
                 for path in (ROOT / "isc_ai").rglob("__init__.py")}
        self.assertEqual(found - declared, set())


if __name__ == "__main__":
    unittest.main()
