"""The packaged harness must be safe to ship to someone else.

The gate itself lives in tools/bundle_gate.py and is imported here on purpose: the tools
decide what may enter the bundle, this decides what may stay, and both must apply the
same rule. A second copy of the rule is a second chance to disagree with it.
"""

import hashlib
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from bundle_gate import CLIENT_NAMES, MACHINE_PATH, gate  # noqa: E402

HARNESS = REPO / "isc_ai" / "resources" / "harness"
MANIFEST = HARNESS / "manifest.json"


class BundleSafetyTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.entries = self.manifest["files"]

    def packaged_files(self):
        for entry in self.entries:
            yield entry["destination"], HARNESS / entry["destination"]

    def test_the_manifest_matches_what_is_packaged(self):
        for destination, path in self.packaged_files():
            with self.subTest(file=destination):
                self.assertTrue(path.is_file(), f"{destination} is declared but not packaged")
                entry = next(item for item in self.entries if item["destination"] == destination)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"],
                                 "the recorded digest must match the packaged bytes, or every "
                                 "installation turns into a conflict")

    def test_no_packaged_file_would_pass_the_gate_with_a_problem(self):
        problems = []
        for destination, path in self.packaged_files():
            problems.extend(gate(destination, path.read_text(encoding="utf-8")))
        self.assertEqual(problems, [], "the bundle ships to other people")

    def test_the_gate_recognizes_what_it_is_meant_to_catch(self):
        # A guard is only worth what its own detection is worth.
        self.assertTrue(gate("x", "| alias | host | someuser:hunter2x |"))
        self.assertTrue(gate("x", "see C:/Users/someone/notes.md"))
        self.assertTrue(gate("x", "the ACHS deployment"))
        self.assertFalse(gate("x", "use `size:exception` when the diff is generated"))
        self.assertFalse(gate("x", "open https://example.invalid/mcp"))

    def test_the_sdd_agents_declare_their_model_role(self):
        # The model line is data in the bundle, not a local edit: that is what keeps the
        # installed copies identical to the package instead of permanently in conflict.
        import re
        agents = [entry["destination"] for entry in self.entries
                  if entry["category"] == "agent" and entry["destination"].startswith("agents/sdd-")]
        self.assertGreaterEqual(len(agents), 8)
        for destination in agents:
            with self.subTest(agent=destination):
                text = (HARNESS / destination).read_text(encoding="utf-8")
                self.assertRegex(text, r"(?m)^model:\s*\S+",
                                 "a packaged sub-agent must carry its model assignment")

    def test_the_doctrine_skills_travel_with_the_bundle(self):
        # The point of genericizing them: TrakCare doctrine stops living on one machine.
        skills = {entry["destination"].split("/")[1] for entry in self.entries
                  if entry["category"] == "skill"}
        for skill in ("intersystems-development", "ccr-alignment", "review-changelist"):
            self.assertIn(skill, skills)

    def test_the_logi_report_skill_travels_and_its_links_resolve(self):
        import re
        packaged = {entry["destination"] for entry in self.entries}
        skill = "skills/logi-report-development/SKILL.md"
        self.assertIn(skill, packaged)
        text = (HARNESS / skill).read_text(encoding="utf-8")
        for link in re.findall(r"\]\(((?:references|assets)/[^)]+)\)", text):
            with self.subTest(link=link):
                self.assertIn(f"skills/logi-report-development/{link}", packaged)


if __name__ == "__main__":
    unittest.main()
