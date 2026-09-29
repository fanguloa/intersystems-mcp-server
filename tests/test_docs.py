"""Contract tests for the managed documentation blocks.

The invariant that matters: everything outside the markers is the author's and is never
touched, and the generated part is derived from the policy so it cannot drift.
"""

import json
import tempfile
import unittest
from pathlib import Path

from isc_ai import config as isc_config
from isc_ai import docs
from isc_ai.config import STATE_DIR

POLICY = {
    "version": 1,
    "site": {"name": "site", "ccr_id": {"prefix": "SITE", "digits": 4}, "protocol_prefix": "SITE"},
    "canonical_base": {"server_id": "my-base", "host": "base.example.invalid", "port": 443,
                       "path_prefix": "/base01", "namespace": "BASE-SITE"},
    "routes": {"direct_ha_nodes": ["10.0.0.1", "10.0.0.2"],
               "alias_constraints": {"my-uat": {"host": "10.0.0.9", "path_prefix": "/uat01"}}},
    "packages": {"BASE-SITE": [{"prefix": "Custom.Site"}]},
    "agents": {"reply_language": "es-CL"},
}

AUTHORED = """# WORKFLOW

## 1. Premisa
Contenido del autor que nadie debe tocar.

## 8. Anexos
Casos documentados.
"""


class DocsTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        (self.project / STATE_DIR).mkdir(parents=True)
        (self.project / STATE_DIR / "config.json").write_text(json.dumps(POLICY), encoding="utf-8")
        self.workflow = self.project / "WORKFLOW.md"
        self.agents = self.project / "AGENTS.md"
        self.workflow.write_text(AUTHORED, encoding="utf-8")
        self.agents.write_text("# AGENTS\n\nEntrada del proyecto.\n", encoding="utf-8")

    def runtime(self):
        return isc_config.load_runtime_config(self.project, environ={})


class RenderTests(DocsTestCase):
    def test_the_topology_block_states_the_policy_that_is_enforced(self):
        body = docs.render_topology(self.runtime())
        for expected in ("my-base", "base.example.invalid", "BASE-SITE", "/base01",
                         "10.0.0.1", "10.0.0.2", "my-uat", "10.0.0.9", "Custom.Site",
                         "SITE followed by four digits"):
            self.assertIn(expected, body, f"the rendered topology must state {expected}")

    def test_the_language_block_renders_the_configured_profile(self):
        body = docs.render_language(self.runtime())
        self.assertIn("español de Chile", body)
        self.assertIn("es-CL", body)
        self.assertIn("isc-ai config set agents.reply_language", body)

    def test_the_chilean_profile_does_not_itself_use_the_voseo_it_forbids(self):
        # Quoted fragments are counter-examples ("no uses 'mirá'"), so only the prose
        # outside quotes is judged.
        import re
        prose = re.sub(r'"[^"]*"', "", docs.render_language(self.runtime()))
        self.assertIn("Responde en español de Chile", prose)
        for forbidden in ("Respondé", "Usá ", "Mirá", "Fijate", "tenés", "podés"):
            self.assertNotIn(forbidden, prose,
                             "the Chilean profile cannot be written in the dialect it rules out")

    def test_another_tag_selects_another_profile(self):
        data = json.loads((self.project / STATE_DIR / "config.json").read_text(encoding="utf-8"))
        data["agents"]["reply_language"] = "en"
        (self.project / STATE_DIR / "config.json").write_text(json.dumps(data), encoding="utf-8")
        body = docs.render_language(self.runtime())
        self.assertIn("Reply in natural English", body)

    def test_an_unknown_tag_is_a_clear_error_not_a_silent_default(self):
        data = json.loads((self.project / STATE_DIR / "config.json").read_text(encoding="utf-8"))
        data["agents"]["reply_language"] = "fr"
        (self.project / STATE_DIR / "config.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(isc_config.ConfigError, "no profile"):
            docs.render_language(self.runtime())

    def test_the_default_is_chilean_spanish_when_nothing_is_configured(self):
        data = json.loads((self.project / STATE_DIR / "config.json").read_text(encoding="utf-8"))
        del data["agents"]
        (self.project / STATE_DIR / "config.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertIn("español de Chile", docs.render_language(self.runtime()))


class BlockTests(DocsTestCase):
    def apply(self):
        return docs.apply(project_dir=self.project, environ={})

    def test_the_authored_content_survives_untouched(self):
        self.apply()
        text = self.workflow.read_text(encoding="utf-8")
        self.assertIn("Contenido del autor que nadie debe tocar.", text)
        self.assertIn("## 8. Anexos", text)
        self.assertTrue(text.startswith(AUTHORED.rstrip("\n")), "the block is appended, not spliced in")

    def test_a_second_run_replaces_the_block_instead_of_stacking_it(self):
        self.apply()
        first = self.workflow.read_text(encoding="utf-8")
        report = self.apply()
        self.assertEqual(self.workflow.read_text(encoding="utf-8"), first, "apply is idempotent")
        self.assertEqual([item["action"] for item in report["blocks"]], ["noop", "noop"])
        self.assertEqual(first.count("<!-- isc-ai:topology -->"), 1)

    def test_a_policy_change_rewrites_only_the_block(self):
        self.apply()
        data = json.loads((self.project / STATE_DIR / "config.json").read_text(encoding="utf-8"))
        data["canonical_base"]["host"] = "moved.example.invalid"
        (self.project / STATE_DIR / "config.json").write_text(json.dumps(data), encoding="utf-8")
        report = self.apply()
        text = self.workflow.read_text(encoding="utf-8")
        self.assertIn("moved.example.invalid", text)
        self.assertNotIn("base.example.invalid", text, "the stale value is gone from the block")
        self.assertIn("Contenido del autor que nadie debe tocar.", text)
        self.assertEqual(report["blocks"][0]["action"], "updated")

    def test_the_plan_writes_nothing(self):
        before = self.workflow.read_bytes()
        report, writes = docs.plan(project_dir=self.project, environ={})
        self.assertTrue(writes)
        self.assertEqual(self.workflow.read_bytes(), before)
        self.assertEqual(report["mode"], "check")

    def test_a_missing_document_is_reported_and_never_created(self):
        self.workflow.unlink()
        report = self.apply()
        self.assertFalse(self.workflow.exists())
        self.assertTrue(any("WORKFLOW.md" in item for item in report["skipped"]))


if __name__ == "__main__":
    unittest.main()
