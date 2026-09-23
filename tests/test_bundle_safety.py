"""The packaged harness must be safe to ship to someone else.

This is the permanent form of the gate in tools/pack_harness.py. That script decides
what may be packed; this test decides what may stay packed, so a file added by hand or
by a future repack cannot quietly carry a credential, a personal path or one client's
identifiers into a bundle that installs on other people's machines.
"""

import json
import re
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent / "isc_ai" / "resources" / "harness"
MANIFEST = HARNESS / "manifest.json"

CREDENTIAL = re.compile(r"\b([a-z][a-z0-9._-]{2,}):([^\s|<>\"'/`]{4,})\b")
CREDENTIAL_SAFE = re.compile(r"(?i)(https?:|isfs:|file:|env:|\{env|\$\{|\{user\}|user:pass|example|schema)")
IN_BACKTICKS = re.compile(r"`[^`]*`")
MACHINE_PATH = re.compile(r"(?i)([A-Za-z]:[\\/]Users[\\/][^\\/\s]+|/home/[^/\s]+|/Users/[^/\s]+)")
CLIENT_NAMES = re.compile(r"(?i)\b(achs|clua|clme|clrs|clcb|cluc|sdsr|sdcq|sdor|sdtl|sdvn|smoc)\w*\b")


def looks_like_a_password(value):
    has_letter = any(char.isalpha() for char in value)
    has_digit = any(char.isdigit() for char in value)
    mixed_case = value != value.lower() and value != value.upper()
    return has_letter and (has_digit or (len(value) >= 8 and mixed_case))


class BundleSafetyTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.entries = self.manifest["files"]

    def packaged_files(self):
        for entry in self.entries:
            path = HARNESS / entry["destination"]
            yield entry["destination"], path

    def test_the_manifest_matches_what_is_packaged(self):
        import hashlib
        for destination, path in self.packaged_files():
            with self.subTest(file=destination):
                self.assertTrue(path.is_file(), f"{destination} is declared but not packaged")
                entry = next(item for item in self.entries if item["destination"] == destination)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"],
                                 "the recorded digest must match the packaged bytes, or every "
                                 "installation turns into a conflict")

    def test_no_packaged_file_carries_a_credential(self):
        for destination, path in self.packaged_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                bare = IN_BACKTICKS.sub("``", line)
                match = CREDENTIAL.search(bare)
                if not match or CREDENTIAL_SAFE.search(bare):
                    continue
                with self.subTest(file=destination, line=number):
                    self.assertFalse(looks_like_a_password(match.group(2)),
                                     f"{destination}:{number} looks like a credential")

    def test_no_packaged_file_carries_a_personal_path(self):
        for destination, path in self.packaged_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                found = MACHINE_PATH.search(line)
                with self.subTest(file=destination, line=number):
                    self.assertIsNone(found, f"{destination}:{number} names a personal home directory")

    def test_no_packaged_file_names_a_client_site(self):
        # The bundle is generic. Client-specific doctrine belongs in the deployment
        # repository, not in a package that installs on someone else's machine.
        for destination, path in self.packaged_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                found = CLIENT_NAMES.search(line)
                with self.subTest(file=destination, line=number):
                    self.assertIsNone(found,
                                      f"{destination}:{number} names client site {found.group(0)!r}"
                                      if found else "")

    def test_the_sdd_agents_declare_their_model_role(self):
        # The model line is data in the bundle, not a local edit: that is what keeps the
        # installed copies identical to the package instead of permanently in conflict.
        agents = [entry["destination"] for entry in self.entries
                  if entry["category"] == "agent" and entry["destination"].startswith("agents/sdd-")]
        self.assertGreaterEqual(len(agents), 8)
        for destination in agents:
            with self.subTest(agent=destination):
                text = (HARNESS / destination).read_text(encoding="utf-8")
                self.assertRegex(text, r"(?m)^model:\s*\S+",
                                 "a packaged sub-agent must carry its model assignment")


if __name__ == "__main__":
    unittest.main()
