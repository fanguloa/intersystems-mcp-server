import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from isc_ai import cli
from isc_ai import setup


class SetupTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.environ = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.root / "xdg")}

    def tearDown(self):
        self.temp.cleanup()

    def run_setup(self, **kwargs):
        defaults = {
            "home": self.home,
            "environ": self.environ,
            "install_harness": False,
            "install_logo": False,
        }
        defaults.update(kwargs)
        return setup.run_setup(**defaults)

    def test_parser_dispatches_setup_as_read_only_by_default(self):
        with mock.patch("isc_ai.cli.run_setup", return_value={"conflicts": []}) as run:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["setup", "--no-logo", "--no-harness"]), 0)
        self.assertFalse(run.call_args.kwargs["apply"])
        self.assertFalse(run.call_args.kwargs["check"])

    def test_parser_rejects_with_opencode_without_apply(self):
        with self.assertRaises(SystemExit):
            cli._parse_cli_args(["setup", "--with-opencode"])

    def test_parser_rejects_non_positive_timeout(self):
        with self.assertRaises(SystemExit):
            cli._parse_cli_args(["setup", "--timeout", "0"])

    def test_default_plan_does_not_write(self):
        report = setup.run_setup(home=self.home, environ=self.environ)
        self.assertEqual(report["mode"], "check")
        self.assertFalse((self.root / "xdg").exists())
        self.assertFalse((self.home / ".claude").exists())

    def test_apply_writes_strict_opencode_config_atomically_and_is_idempotent(self):
        config_path = self.root / "custom" / "opencode.json"
        report = self.run_setup(apply=True, configure_opencode=True, opencode_config=config_path)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(data["$schema"], setup.SCHEMA_URL)
        self.assertEqual(data["mcp"][setup.MCP_NAME], {
            "type": "local",
            "command": ["isc-ai", "mcp"],
            "enabled": True,
            "environment": {"IRIS_MCP_TIMEOUT": "1800"},
        })
        self.assertFalse(config_path.with_name(".opencode.json.isc-ai.tmp").exists())
        second = self.run_setup(apply=True, configure_opencode=True, opencode_config=config_path)
        self.assertEqual(second["actions"][0]["action"], "noop")
        self.assertTrue(report["restart_required"])

    def test_atomic_write_preserves_old_predictable_temp_named_file(self):
        target = self.root / "atomic" / "config.json"
        target.parent.mkdir()
        old_temp = target.with_name(f".{target.name}.isc-ai.tmp")
        old_temp.write_bytes(b"pre-existing")

        setup._atomic_write(target, b"new content")

        self.assertEqual(target.read_bytes(), b"new content")
        self.assertEqual(old_temp.read_bytes(), b"pre-existing")

    @unittest.skipIf(os.name == "nt", "POSIX mode preservation")
    def test_atomic_write_preserves_existing_file_mode_when_mode_is_omitted(self):
        target = self.root / "atomic-mode" / "config.json"
        target.parent.mkdir()
        target.write_bytes(b"old")
        target.chmod(0o640)

        setup._atomic_write(target, b"new")

        self.assertEqual(target.read_bytes(), b"new")
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_post_replace_failure_is_registered_and_removes_new_target(self):
        target = self.root / "post-replace" / "config.json"
        target.parent.mkdir()
        write = setup.ManagedWrite(target.parent, target, b"replacement")
        snapshots = setup._preflight_writes([write])
        real_atomic_write = setup._atomic_write

        def fail_after_replace(*args, **kwargs):
            real_atomic_write(*args, **kwargs)
            raise RuntimeError("post-replace verification failure")

        with mock.patch("isc_ai.setup._atomic_write", side_effect=fail_after_replace):
            with self.assertRaisesRegex(setup.SetupError, "post-replace verification failure"):
                setup._apply_transaction([write], external_install=False, snapshots=snapshots)

        self.assertFalse(target.exists())

    def test_atomic_write_attempts_directory_fsync(self):
        target = self.root / "atomic-fsync" / "config.json"
        with mock.patch("isc_ai.setup._fsync_directory") as fsync_directory:
            setup._atomic_write(target, b"new")
        fsync_directory.assert_called_once_with(target.parent)

    def test_merge_preserves_unknown_fields_and_removes_only_stale_environment(self):
        original = {
            "theme": "custom",
            "mcp": {
                "other": {"type": "remote", "url": "https://example.invalid"},
                setup.MCP_NAME: {
                    "custom": 7,
                    "cwd": ".",
                    "enabled": False,
                    "environment": {"KEEP": "yes", "IRIS_MCP_CONFIG": "old"},
                },
            },
        }
        merged, changed = setup.merge_opencode_config(original, timeout=9)
        self.assertTrue(changed)
        self.assertEqual(merged["theme"], "custom")
        self.assertEqual(merged["mcp"]["other"], original["mcp"]["other"])
        self.assertEqual(merged["mcp"][setup.MCP_NAME]["custom"], 7)
        self.assertNotIn("cwd", merged["mcp"][setup.MCP_NAME])
        self.assertEqual(merged["mcp"][setup.MCP_NAME]["environment"], {"KEEP": "yes", "IRIS_MCP_TIMEOUT": "9"})

    def test_schema_only_change_is_reported_and_applied(self):
        path = self.root / "opencode.json"
        entry = setup.opencode_mcp_entry(timeout=1800)
        path.write_text(json.dumps({"mcp": {setup.MCP_NAME: entry}}), encoding="utf-8")
        report = self.run_setup(apply=True, configure_opencode=True, opencode_config=path)
        self.assertEqual(report["actions"][0]["action"], "update")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["$schema"], setup.SCHEMA_URL)

    def test_malformed_shapes_are_rejected(self):
        for data, expected in (({"mcp": []}, "'mcp' must be an object"),
                               ({"mcp": {}, "plugin": {}}, "'plugin' must be an array")):
            with self.subTest(data=data):
                with self.assertRaisesRegex(setup.SetupError, expected):
                    setup.merge_opencode_config(data)
        with self.assertRaisesRegex(setup.SetupError, "environment must be an object"):
            setup.merge_opencode_config({"mcp": {setup.MCP_NAME: {"environment": []}}})
        with self.assertRaisesRegex(setup.SetupError, "'plugin' must be an array"):
            setup.merge_tui_config({"plugin": {}}, "logo.tsx")

    def test_jsonc_is_byte_preserved_and_returns_exact_manual_snippet(self):
        path = self.root / "opencode.jsonc"
        original = b'{\n  // keep me\n  "theme": "x",\n}\n'
        path.write_bytes(original)
        report = self.run_setup(apply=True, configure_opencode=True, opencode_config=path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(report["manual"]), 1)
        snippet = json.loads(report["manual"][0]["snippet"])
        self.assertEqual(snippet["mcp"][setup.MCP_NAME]["command"], ["isc-ai", "mcp"])

    def test_default_opencode_config_ambiguity_requires_explicit_path(self):
        config_dir = setup.opencode_config_dir(self.home, self.environ)
        config_dir.mkdir(parents=True)
        (config_dir / "opencode.json").write_text("{}", encoding="utf-8")
        (config_dir / "opencode.jsonc").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(setup.SetupError, "both .* exist"):
            self.run_setup(configure_opencode=True)

    def test_claude_project_must_exist_and_unknown_fields_are_preserved(self):
        missing = self.root / "typo"
        with self.assertRaisesRegex(setup.SetupError, "existing directory"):
            self.run_setup(claude_project=missing, configure_opencode=False)
        project = self.root / "project"
        project.mkdir()
        path = project / ".mcp.json"
        path.write_text(json.dumps({"custom": 1, "mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
        self.run_setup(apply=True, claude_project=project, configure_opencode=False)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["custom"], 1)
        self.assertIn("other", data["mcpServers"])
        self.assertEqual(data["mcpServers"][setup.MCP_NAME]["args"], ["mcp"])

    def test_logo_install_noop_and_user_modified_conflict(self):
        first = setup.run_setup(
            apply=True, home=self.home, environ=self.environ,
            configure_opencode=True, install_logo=True, install_harness=False,
        )
        config_dir = setup.opencode_config_dir(self.home, self.environ)
        # TUI plugins live in tui-plugins/ and are registered in tui.json: opencode.json
        # is for runtime plugins and its TUI would never load this file.
        target = config_dir / "tui-plugins" / "isc-logo.tsx"
        self.assertEqual(target.read_bytes(), setup.LOGO_RESOURCE.read_bytes())
        tui = json.loads((config_dir / "tui.json").read_text(encoding="utf-8"))
        self.assertIn(str(target), tui["plugin"])
        config = json.loads((config_dir / "opencode.json").read_text(encoding="utf-8"))
        self.assertNotIn("plugin", config, "the logo must not be registered as a runtime plugin")
        second = setup.run_setup(
            apply=True, home=self.home, environ=self.environ,
            configure_opencode=True, install_logo=True, install_harness=False,
        )
        logo_actions = [item for item in second["actions"] if item.get("category") == "logo"]
        self.assertEqual(logo_actions[0]["action"], "noop")
        target.write_text("user version", encoding="utf-8")
        conflict = setup.run_setup(
            apply=True, home=self.home, environ=self.environ,
            configure_opencode=True, install_logo=True, install_harness=False,
        )
        self.assertIn(str(target), conflict["conflicts"])
        self.assertEqual(target.read_text(encoding="utf-8"), "user version")
        self.assertFalse(first["conflicts"])

    def test_a_stale_logo_entry_in_opencode_config_is_removed(self):
        config_dir = setup.opencode_config_dir(self.home, self.environ)
        config_dir.mkdir(parents=True)
        (config_dir / "opencode.json").write_text(
            json.dumps({"plugin": ["./plugins/isc-logo.tsx", "keep-me"]}), encoding="utf-8")

        setup.run_setup(
            apply=True, home=self.home, environ=self.environ,
            configure_opencode=True, install_logo=True, install_harness=False,
        )

        config = json.loads((config_dir / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(config["plugin"], ["keep-me"], "an earlier version registered it in the wrong file")

    def test_conflicting_unregistered_logo_is_not_newly_registered(self):
        config_dir = setup.opencode_config_dir(self.home, self.environ)
        target = config_dir / "tui-plugins" / "isc-logo.tsx"
        target.parent.mkdir(parents=True)
        target.write_text("user version", encoding="utf-8")

        report = setup.run_setup(
            apply=True, home=self.home, environ=self.environ,
            configure_opencode=True, install_logo=True, install_harness=False,
        )

        self.assertIn(str(target), report["conflicts"])
        self.assertFalse((config_dir / "tui.json").exists(), "a conflicting logo is not registered")
        self.assertEqual(target.read_text(encoding="utf-8"), "user version")

    def test_logo_content_install_requires_restart_when_config_is_unchanged(self):
        config_dir = setup.opencode_config_dir(self.home, self.environ)
        config_dir.mkdir(parents=True)
        current, _ = setup.merge_opencode_config({})
        (config_dir / "opencode.json").write_text(json.dumps(current), encoding="utf-8")

        report = setup.run_setup(
            apply=True, home=self.home, environ=self.environ,
            configure_opencode=True, install_logo=True, install_harness=False,
        )

        config_actions = [item for item in report["actions"] if item["target"].endswith("opencode.json")]
        self.assertEqual(config_actions[0]["action"], "noop")
        self.assertTrue(report["restart_required"])

    def _harness(self, content=b"v1", *, destination="skills/demo/SKILL.md", digest=None):
        root = self.root / "bundle"
        source = root / "payload.md"
        root.mkdir(exist_ok=True)
        source.write_bytes(content)
        digest = digest or hashlib.sha256(content).hexdigest()
        manifest = {
            "schema": "isc-ai.harness-manifest.v1",
            "bundle_version": 1,
            "files": [{
                "category": "skill", "source": "payload.md",
                "destination": destination, "sha256": digest,
            }],
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return root, manifest_path, source

    def test_harness_initial_noop_safe_upgrade_conflict_and_obsolete(self):
        root, manifest_path, source = self._harness()
        patches = mock.patch.multiple(setup, HARNESS_ROOT=root, HARNESS_MANIFEST=manifest_path)
        with patches:
            first = setup.run_setup(apply=True, home=self.home, environ=self.environ, configure_opencode=False, install_logo=False)
            target = self.home / ".claude" / "skills" / "demo" / "SKILL.md"
            self.assertEqual(target.read_bytes(), b"v1")
            second = setup.run_setup(apply=True, home=self.home, environ=self.environ, configure_opencode=False, install_logo=False)
            self.assertIn("noop", [item["action"] for item in second["actions"]])

            source.write_bytes(b"v2")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["bundle_version"] = 2
            manifest["files"][0]["sha256"] = hashlib.sha256(b"v2").hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            upgraded = setup.run_setup(apply=True, home=self.home, environ=self.environ, configure_opencode=False, install_logo=False)
            self.assertIn("upgrade", [item["action"] for item in upgraded["actions"]])
            self.assertEqual(target.read_bytes(), b"v2")

            target.write_bytes(b"user")
            source.write_bytes(b"v3")
            manifest["bundle_version"] = 3
            manifest["files"][0]["sha256"] = hashlib.sha256(b"v3").hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            conflicted = setup.run_setup(apply=True, home=self.home, environ=self.environ, configure_opencode=False, install_logo=False)
            self.assertIn(str(target), conflicted["conflicts"])
            self.assertEqual(target.read_bytes(), b"user")

            manifest["bundle_version"] = 4
            manifest["files"] = []
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            obsolete = setup.run_setup(home=self.home, environ=self.environ, configure_opencode=False, install_logo=False)
            self.assertIn(str(target), obsolete["obsolete"])
            self.assertTrue(target.exists())
        self.assertFalse(first["conflicts"])

    def test_harness_rejects_traversal_and_absolute_paths(self):
        for destination in ("../escape", str((self.root / "absolute").resolve())):
            root, manifest_path, _ = self._harness(destination=destination)
            with self.subTest(destination=destination), mock.patch.multiple(
                setup, HARNESS_ROOT=root, HARNESS_MANIFEST=manifest_path
            ):
                with self.assertRaisesRegex(setup.SetupError, "safe relative path"):
                    setup.load_harness_manifest()

    def test_transaction_rolls_back_prior_writes_after_injected_failure(self):
        project = self.root / "project"
        project.mkdir()
        claude_path = project / ".mcp.json"
        original = b'{"keep": true}\n'
        claude_path.write_bytes(original)
        config_path = self.root / "custom" / "opencode.json"
        real_replace = os.replace
        calls = 0

        def fail_second(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected write failure")
            real_replace(source, target)

        with mock.patch("isc_ai.setup.os.replace", side_effect=fail_second):
            with self.assertRaisesRegex(setup.SetupError, "prior managed files were rolled back"):
                self.run_setup(
                    apply=True, configure_opencode=True, opencode_config=config_path,
                    claude_project=project,
                )

        self.assertFalse(config_path.exists())
        self.assertEqual(claude_path.read_bytes(), original)
        self.assertFalse(setup.state_path(self.home, self.environ).exists())
        residue = list(self.root.rglob("*.isc-ai.*.tmp"))
        self.assertEqual(residue, [])
        self.assertFalse((self.root / "xdg" / "isc-ai" / ".setup.lock").exists())

    def test_external_edit_after_preflight_is_not_overwritten_and_prior_write_rolls_back(self):
        project = self.root / "project"
        project.mkdir()
        claude_path = project / ".mcp.json"
        claude_path.write_bytes(b'{"original": true}\n')
        external = b'{"external": true}\n'
        config_path = self.root / "custom" / "opencode.json"

        def external_install():
            claude_path.write_bytes(external)
            return True

        with mock.patch("isc_ai.setup._install_opencode", side_effect=external_install):
            with self.assertRaisesRegex(setup.SetupError, "changed after preflight"):
                self.run_setup(
                    apply=True, configure_opencode=True, opencode_config=config_path,
                    claude_project=project, with_opencode=True,
                )

        self.assertEqual(claude_path.read_bytes(), external)
        self.assertFalse(config_path.exists())
        self.assertFalse(setup.state_path(self.home, self.environ).exists())
        self.assertFalse((self.root / "xdg" / "isc-ai" / ".setup.lock").exists())

    def test_existing_setup_lock_fails_before_external_install_or_writes(self):
        lock = self.root / "xdg" / "isc-ai" / ".setup.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text("existing", encoding="utf-8")
        config_path = self.root / "custom" / "opencode.json"

        with mock.patch("isc_ai.setup._install_opencode") as install:
            with self.assertRaisesRegex(setup.SetupError, "another isc-ai setup apply is active"):
                self.run_setup(
                    apply=True, configure_opencode=True, opencode_config=config_path,
                    with_opencode=True,
                )

        install.assert_not_called()
        self.assertEqual(lock.read_text(encoding="utf-8"), "existing")
        self.assertFalse(config_path.exists())

    @unittest.skipUnless(os.name == "nt", "Windows read-only handling")
    def test_windows_read_only_target_fails_preflight_without_altering_file(self):
        target = self.root / "readonly.json"
        target.write_bytes(b"original")
        target.chmod(stat.S_IREAD)
        try:
            write = setup.ManagedWrite(target.parent, target, b"replacement")
            with self.assertRaisesRegex(setup.SetupError, "read-only"):
                setup._preflight_writes([write])
            self.assertEqual(target.read_bytes(), b"original")
        finally:
            target.chmod(stat.S_IWRITE)

    def test_external_install_runs_before_writes_and_failure_leaves_files_untouched(self):
        config_path = self.root / "custom" / "opencode.json"
        with mock.patch("isc_ai.setup._install_opencode", side_effect=setup.SetupError("install failed")):
            with self.assertRaisesRegex(setup.SetupError, "install failed"):
                self.run_setup(
                    apply=True, configure_opencode=True, opencode_config=config_path,
                    with_opencode=True,
                )
        self.assertFalse(config_path.exists())
        self.assertFalse(setup.state_path(self.home, self.environ).exists())

    def test_write_failure_reports_external_install_as_not_rollbackable(self):
        config_path = self.root / "custom" / "opencode.json"
        with mock.patch("isc_ai.setup._install_opencode") as install:
            with mock.patch("isc_ai.setup._atomic_write", side_effect=OSError("injected")):
                with self.assertRaisesRegex(setup.SetupError, "package-manager install is not rollbackable"):
                    self.run_setup(
                        apply=True, configure_opencode=True, opencode_config=config_path,
                        with_opencode=True,
                    )
        install.assert_called_once_with()
        self.assertFalse(config_path.exists())

    def test_relative_xdg_config_home_is_rejected_before_writes(self):
        environ = {"HOME": str(self.home), "XDG_CONFIG_HOME": "relative/config"}
        with self.assertRaisesRegex(setup.SetupError, "must be an absolute path"):
            setup.run_setup(apply=True, home=self.home, environ=environ)
        self.assertFalse((self.home / ".claude").exists())

    def test_harness_target_cannot_escape_through_parent_symlink(self):
        root, manifest_path, _ = self._harness()
        claude = self.home / ".claude"
        claude.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (claude / "skills").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        with mock.patch.multiple(setup, HARNESS_ROOT=root, HARNESS_MANIFEST=manifest_path):
            with self.assertRaisesRegex(setup.SetupError, "escapes its root"):
                setup.run_setup(
                    apply=True, home=self.home, environ=self.environ,
                    configure_opencode=False, install_logo=False,
                )
        self.assertEqual(list(outside.iterdir()), [])

    def test_managed_file_target_symlink_is_rejected(self):
        config_dir = setup.opencode_config_dir(self.home, self.environ)
        target = config_dir / "plugins" / "isc-logo.tsx"
        target.parent.mkdir(parents=True)
        outside = self.root / "outside-logo.tsx"
        outside.write_text("user", encoding="utf-8")
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"file symlinks unavailable: {exc}")
        with self.assertRaisesRegex(setup.SetupError, "must not be a symlink"):
            setup.run_setup(
                apply=True, home=self.home, environ=self.environ,
                configure_opencode=True, install_logo=True, install_harness=False,
            )
        self.assertEqual(outside.read_text(encoding="utf-8"), "user")

    def test_packaged_sdd_agents_and_fallback_commands_have_dependency_closure(self):
        shared = "~/.claude/skills/_shared/sdd-phase-common.md"
        for directory in (setup.HARNESS_ROOT / "agents", setup.HARNESS_ROOT / "commands"):
            for path in directory.glob("sdd-*.md"):
                with self.subTest(path=path):
                    text = path.read_text(encoding="utf-8")
                    self.assertIn(shared, text)
                    phase = path.stem
                    self.assertIn(f"~/.claude/skills/{phase}/SKILL.md", text)

    def test_a_conflicting_harness_file_is_adopted_only_when_asked(self):
        """A file this tool did not write is never overwritten without an explicit flag.

        Adoption is the way out of a permanent conflict: the bundle wins once, its digest
        is recorded, and later upgrades become ordinary upgrades instead of conflicts.
        """
        target = self.home / ".claude" / "agents" / "sdd-apply.md"
        target.parent.mkdir(parents=True)
        target.write_text("installed by something else", encoding="utf-8")

        plan = setup.run_setup(
            apply=True, home=self.home, environ=self.environ,
            configure_opencode=False, install_logo=False, install_harness=True,
        )
        self.assertIn(str(target), plan["conflicts"])
        self.assertEqual(target.read_text(encoding="utf-8"), "installed by something else")
        self.assertEqual(plan["adopted"], [])

        adopted = setup.run_setup(
            apply=True, home=self.home, environ=self.environ,
            configure_opencode=False, install_logo=False, install_harness=True,
            adopt_harness=True,
        )
        packaged = (setup.HARNESS_ROOT / "agents" / "sdd-apply.md").read_bytes()
        self.assertIn(str(target), adopted["adopted"])
        self.assertNotIn(str(target), adopted["conflicts"])
        self.assertEqual(target.read_bytes(), packaged)

        # The baseline is recorded, so the next run is a no-op rather than a conflict.
        again = setup.run_setup(
            apply=True, home=self.home, environ=self.environ,
            configure_opencode=False, install_logo=False, install_harness=True,
        )
        actions = {item["target"]: item["action"] for item in again["actions"]}
        self.assertEqual(actions[str(target)], "noop")
        self.assertEqual(again["conflicts"], [])

    def test_a_transient_sharing_violation_is_retried_instead_of_failing(self):
        """Windows raises access denied when another process holds the file for an instant.

        An antivirus or the search indexer opening the temporary file microseconds after it
        is created made one run in six fail before this retry existed. The failure is
        transient, so it is retried; anything that is not transient still propagates.
        """
        target = self.root / "retry.json"
        attempts = []
        real_replace = os.replace

        def flaky(source, destination):
            attempts.append(destination)
            if len(attempts) < 3:
                error = PermissionError("Access is denied")
                error.winerror = 5
                raise error
            return real_replace(source, destination)

        with mock.patch("isc_ai.setup.os.replace", side_effect=flaky), \
             mock.patch("isc_ai.setup.time.sleep"):
            setup._atomic_write(target, b"written")
        self.assertEqual(target.read_bytes(), b"written")
        self.assertEqual(len(attempts), 3, "it must retry, not give up on the first refusal")

    def test_a_permission_error_that_is_not_transient_is_not_retried(self):
        target = self.root / "hard-fail.json"
        attempts = []

        def denied(source, destination):
            attempts.append(destination)
            error = PermissionError("not a sharing problem")
            error.winerror = 1314  # a privilege the process does not hold
            raise error

        with mock.patch("isc_ai.setup.os.replace", side_effect=denied), \
             mock.patch("isc_ai.setup.time.sleep"):
            with self.assertRaises(PermissionError):
                setup._atomic_write(target, b"written")
        self.assertEqual(len(attempts), 1, "retrying a real permission problem only hides it")
        self.assertFalse(target.exists())

    def test_install_sh_documents_flags_and_rejects_unknown_arguments_before_uv(self):
        text = (Path(__file__).parents[1] / "install.sh").read_text(encoding="utf-8")
        self.assertIn("Usage: ./install.sh [--apply]", text)
        self.assertIn("Unknown argument", text)
        self.assertLess(text.index("Unknown argument"), text.index("command -v uv"))

    def test_with_opencode_is_called_only_in_explicit_apply(self):
        with mock.patch("isc_ai.setup._install_opencode") as install:
            self.run_setup(apply=True, configure_opencode=False, with_opencode=True)
        install.assert_called_once_with()
        with self.assertRaisesRegex(setup.SetupError, "requires --apply"):
            self.run_setup(configure_opencode=False, with_opencode=True)

    def test_no_opencode_does_not_add_mcp_while_logo_stays_schema_valid(self):
        setup.run_setup(
            apply=True, home=self.home, environ=self.environ,
            configure_opencode=False, install_logo=True, install_harness=False,
        )
        config_dir = setup.opencode_config_dir(self.home, self.environ)
        data = json.loads((config_dir / "opencode.json").read_text(encoding="utf-8"))
        self.assertNotIn("mcp", data)
        self.assertNotIn("plugin", data)
        self.assertEqual(data["$schema"], setup.SCHEMA_URL)
        tui = json.loads((config_dir / "tui.json").read_text(encoding="utf-8"))
        self.assertEqual(tui["$schema"], setup.TUI_SCHEMA_URL)
        self.assertEqual([Path(item).name for item in tui["plugin"]], ["isc-logo.tsx"])


if __name__ == "__main__":
    unittest.main()
