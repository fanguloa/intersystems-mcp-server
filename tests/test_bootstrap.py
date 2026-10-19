"""Contract of the one-line installers in bootstrap/.

They are fetched and run with 'irm | iex' and 'curl | bash', so they must stay ASCII, must
not end the caller's session, must register exactly the servers of the suite baseline and
must agree with each other on the pinned versions.
"""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import build_pages

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "bootstrap" / "install.ps1"
SH = ROOT / "bootstrap" / "install.sh"
SUITE = json.loads((ROOT / "isc_ai" / "resources" / "mcp-suite.json").read_text(encoding="utf-8"))


class BootstrapTextTests(unittest.TestCase):
    def test_both_scripts_are_ascii(self):
        for path in (PS1, SH):
            with self.subTest(script=path.name):
                self.assertTrue(all(b < 128 for b in path.read_bytes()), "non-ASCII byte")

    def test_the_shell_script_uses_lf(self):
        self.assertNotIn(b"\r", SH.read_bytes())

    def test_the_powershell_script_never_exits(self):
        # Under 'irm | iex' an 'exit' closes the user's terminal.
        code = [line.split("#", 1)[0] for line in PS1.read_text(encoding="ascii").splitlines()]
        self.assertFalse([line for line in code if re.search(r"(^|[;{\s])exit(\s|$)", line)])

    def test_both_scripts_register_the_whole_suite(self):
        expected = sorted(SUITE["servers"])
        ps1 = re.search(r'\$suite = @\(([^)]*)\)', PS1.read_text(encoding="ascii")).group(1)
        sh = re.search(r"for s in ((?:[a-z0-9-]+ ?)+); do", SH.read_text(encoding="ascii")).group(1)
        self.assertEqual(sorted(re.findall(r'"([^"]+)"', ps1)), expected)
        self.assertEqual(sorted(sh.split()), expected)

    def test_both_scripts_pin_the_same_engram(self):
        ps1 = re.search(r'\$EngramVersion = "([^"]+)"', PS1.read_text(encoding="ascii")).group(1)
        sh = re.search(r'ENGRAM_VERSION="([^"]+)"', SH.read_text(encoding="ascii")).group(1)
        self.assertEqual(ps1, sh)


class BootstrapSyntaxTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("powershell") or shutil.which("pwsh"), "PowerShell not available")
    def test_the_powershell_script_parses(self):
        shell = shutil.which("powershell") or shutil.which("pwsh")
        probe = ("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                 f"'{PS1}', [ref]$null, [ref]$e); $e.Count")
        out = subprocess.run([shell, "-NoProfile", "-Command", probe], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.stdout.strip(), "0", out.stderr)

    @unittest.skipUnless(shutil.which("bash"), "bash not available")
    def test_the_shell_script_parses(self):
        # The script goes through stdin: a Windows path would mean nothing to WSL's bash.
        out = subprocess.run(["bash", "-n"], input=SH.read_bytes(), capture_output=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr.decode("utf-8", "replace"))


class PagesTests(unittest.TestCase):
    def test_pages_publish_the_scripts_with_their_digests(self):
        ref = re.search(r'ISC_AI_REF = "([^"]+)"', PS1.read_text(encoding="ascii")).group(1)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "public"
            digests = build_pages.build(ref, out, "https://pages.example.invalid/isc-ai")
            for name in ("install.ps1", "install.sh"):
                self.assertEqual((out / name).read_bytes(), (ROOT / "bootstrap" / name).read_bytes())
                self.assertIn(digests[name], (out / f"{name}.sha256").read_text(encoding="ascii"))
            self.assertIn("irm https://pages.example.invalid/isc-ai/install.ps1 | iex",
                          (out / "index.html").read_text(encoding="utf-8"))

    def test_pages_refuse_a_tag_the_scripts_do_not_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                build_pages.build("v0.0.0-not-this", Path(tmp) / "public")


if __name__ == "__main__":
    unittest.main()
