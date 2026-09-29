"""Vendor (sync) skills from an external Claude Code marketplace into the isc-ai harness.

isc-ai's own doctrine skills are added by tools/add_doctrine_skills.py, which copies from a
personal reference installation. This tool answers a different question: how does a THIRD
PARTY's skill catalog -- reviewed, pinned to an exact commit, plugin by plugin -- become part
of the harness bundle without isc-ai adopting a second plugin/marketplace mechanism at
runtime? The isc-ai harness install stays the single source of truth for every client; the
marketplace is only ever a build-time input, vendored (like Go's vendor/ directory) at a
pinned commit so a later, unreviewed marketplace change cannot silently change what ships.
Every vendored file still passes the same packaging gate (tools/bundle_gate.py) that
add_doctrine_skills.py enforces, and a name collision with a skill this tool did not
already vendor under that same plugin is refused rather than silently overwritten --
vendoring must never clobber isc-ai's own doctrine, nor a different plugin's content.
Provenance (source URL, pinned commit, per-plugin version and audience, and the fact that the
marketplace ships no LICENSE file) is recorded in vendor-provenance.json and mirrored onto each
vendored file's own manifest entry, so a later slice (package `owner`, the technical-extension
split) can find and move this content without re-deriving where it came from.

Usage (from the repository root):
    python tools/sync_marketplace.py --source <path-or-git-url> --commit <40-hex> \\
        --repo-url <canonical origin> --plugin caveman-enterprise --plugin uifw \\
        [--audience uifw=technical] [--marketplace <provenance key>] [--apply]

`--source` may be a local git checkout (used as-is, its HEAD is verified against --commit) or
a git URL (cloned into a fresh temporary directory, then checked out at --commit). Cloning from
a URL needs network access and is therefore not exercised by the hermetic test suite; the local
checkout path is what tests/test_sync_marketplace.py verifies against real git plumbing.
"""

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bundle_gate import gate

REPO = pathlib.Path(__file__).resolve().parent.parent
HARNESS = REPO / "isc_ai" / "resources" / "harness"
MANIFEST = HARNESS / "manifest.json"
PROVENANCE = HARNESS / "vendor-provenance.json"

COMMIT_RE = re.compile(r"[0-9a-f]{40}")
GIT_URL_RE = re.compile(r"^(https?://|git@|ssh://|git://)")

# TODO(licensing): the vendored AI Enablement marketplace skills have no upstream LICENSE
# file. Confirm with ai-enablement@intersystems.com that isc-ai may redistribute them, then
# record the answer here and in LICENSE_NOTE (which feeds vendor-provenance.json).
LICENSE_NOTE = (
    "No LICENSE file in the source repository; InterSystems-internal content; "
    "redistribution confirmation pending with ai-enablement@intersystems.com."
)


class SyncError(Exception):
    """Raised for any condition that must abort the sync before it writes anything."""


def _run_git(args, cwd=None):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SyncError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def resolve_source(source, commit):
    """Return a local Path checked out at exactly `commit`, or raise SyncError.

    A local path must already be a git checkout at that commit. A git URL is cloned into a
    fresh temporary directory first (network required; not covered by the hermetic tests).
    """
    if not COMMIT_RE.fullmatch(commit or ""):
        raise SyncError(f"pinned commit must be 40 hex characters, got {commit!r}")

    if GIT_URL_RE.match(source):
        workdir = pathlib.Path(tempfile.mkdtemp(prefix="isc-ai-sync-marketplace-"))
        _run_git(["clone", source, str(workdir)])
        _run_git(["checkout", commit], cwd=workdir)
        root = workdir
    else:
        root = pathlib.Path(source)
        if not (root / ".git").exists():
            raise SyncError(f"source is not a git checkout: {root}")

    actual = _run_git(["rev-parse", "HEAD"], cwd=root)
    if actual != commit:
        raise SyncError(f"pinned commit mismatch: expected {commit}, checkout is at {actual}")
    return root


def plugin_version(root, plugin):
    path = root / "plugins" / plugin / ".claude-plugin" / "plugin.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"cannot read plugin manifest {path}: {exc}") from None
    version = data.get("version")
    if not version:
        raise SyncError(f"plugin {plugin!r} has no version declared in {path}")
    return version


def discover_skills(root, plugin):
    """Return sorted [(skill_name, skill_dir)] for one plugin.

    Each direct subdirectory of plugins/<plugin>/skills/ that contains a SKILL.md is one
    skill, matching the Claude Code skill directory shape isc-ai's own bundle already uses.
    """
    skills_root = root / "plugins" / plugin / "skills"
    if not skills_root.is_dir():
        raise SyncError(f"plugin {plugin!r} has no skills/ directory at {skills_root}")
    found = [
        (entry.name, entry)
        for entry in sorted(skills_root.iterdir())
        if entry.is_dir() and (entry / "SKILL.md").is_file()
    ]
    if not found:
        raise SyncError(f"plugin {plugin!r} declares no skills under {skills_root}")
    return found


def load_manifest(manifest_path=None):
    path = manifest_path or MANIFEST
    return json.loads(path.read_text(encoding="utf-8"))


def load_provenance(provenance_path=None):
    path = provenance_path or PROVENANCE
    if not path.exists():
        return {"schema": "isc-ai.harness-vendor-provenance.v1", "sources": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = data.get("sources")
    if not isinstance(sources, dict):
        sources = {}
    return {"schema": data.get("schema", "isc-ai.harness-vendor-provenance.v1"), "sources": sources}


def _read_blob(root, commit, relative_path):
    """Return the exact bytes git stored for `relative_path` at `commit`.

    Uses `git cat-file blob`, a plumbing command that returns the object exactly as
    stored -- unlike reading the working tree file with `path.read_bytes()`, this is not
    affected by a local checkout's core.autocrlf/eol line-ending translation. Without
    this, a clone made under core.autocrlf=true would silently vendor CRLF-translated
    bytes and the recorded sha256 would depend on the machine that ran the sync rather
    than on the pinned commit.
    """
    result = subprocess.run(
        ["git", "cat-file", "blob", f"{commit}:{relative_path}"],
        cwd=root, capture_output=True,
    )
    if result.returncode != 0:
        raise SyncError(
            f"cannot read {relative_path!r} from git blob at {commit}: "
            f"{result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return result.stdout


def _refuse_unsafe_member(path, skill_dir, *, is_symlink):
    """Return a problem string if `path` (a member of `skill_dir`) must be refused, or
    None if it is safe to vendor.

    Two independent checks, because either alone misses a case the other catches:
    a symlink is refused outright regardless of where it points (vendoring must never
    ship a link, even one that happens to point back inside skill_dir); a path that
    resolves outside skill_dir is refused even when it is not itself a symlink -- this
    is how `rglob` yields the *files* reached through a symlinked ancestor directory
    (those file entries are not symlinks themselves, only an ancestor is).
    """
    if is_symlink:
        return f"{path}: symlinks are not allowed in vendored skill content"
    resolved = path.resolve()
    try:
        resolved.relative_to(skill_dir.resolve())
    except ValueError:
        return f"{path}: resolves outside its own skill directory"
    return None


def known_vendored_skills(provenance, manifest):
    """Map skill directory name -> the plugin that vendored it, from either record --
    the provenance file is the primary source, the manifest's own `vendor` entries are a
    fallback in case provenance was ever hand-edited away.

    Scoped by plugin (not just by name) so that a *different* plugin declaring a skill
    with the same directory name as one this tool already vendored is still treated as
    a collision -- only a re-sync of the *same* plugin's own, already-vendored skill is
    idempotent.
    """
    owners = {}
    for source in provenance.get("sources", {}).values():
        for plugin_entry in source.get("plugins", []):
            plugin_name = plugin_entry.get("name")
            for skill in plugin_entry.get("skills", []):
                owners[skill] = plugin_name
    for entry in manifest.get("files", []):
        vendor = entry.get("vendor")
        if entry.get("category") == "skill" and vendor:
            parts = entry["destination"].split("/")
            if len(parts) >= 2:
                owners.setdefault(parts[1], vendor.get("plugin"))
    return owners


class PlanResult:
    def __init__(self):
        self.candidates = []       # [(destination, raw_bytes, plugin, skill_name), ...]
        self.problems = []         # packaging-gate findings, as strings
        self.collisions = []       # skill names that collide with a differently-owned skill
        self.plugin_versions = {}  # plugin -> version string
        self.skills_by_plugin = {}  # plugin -> [skill_name, ...] (only the non-colliding ones)

    @property
    def ok(self):
        return not self.problems and not self.collisions


def plan(root, plugins, *, commit, harness_dir=None, manifest=None, provenance=None):
    """Compute what a sync of `plugins` (from the marketplace checkout at `root`, pinned
    at `commit`) would do, without writing anything. Always safe to call, including with
    --apply omitted.

    `commit` is required (not inferred from `root`'s current HEAD) so every candidate's
    bytes are read from that exact git blob via `_read_blob`, never from whatever a
    particular local checkout happens to have translated onto disk.
    """
    harness_dir = harness_dir or HARNESS
    manifest = manifest if manifest is not None else load_manifest()
    provenance = provenance if provenance is not None else load_provenance()
    known = known_vendored_skills(provenance, manifest)

    result = PlanResult()
    for plugin in sorted(set(plugins)):
        result.plugin_versions[plugin] = plugin_version(root, plugin)
        skill_names = []
        for skill_name, skill_dir in discover_skills(root, plugin):
            target_dir = harness_dir / "skills" / skill_name
            owner = known.get(skill_name)
            if target_dir.exists() and owner != plugin:
                result.collisions.append(skill_name)
                continue
            skill_names.append(skill_name)
            for path in sorted(skill_dir.rglob("*")):
                unsafe = _refuse_unsafe_member(path, skill_dir, is_symlink=path.is_symlink())
                if unsafe:
                    result.problems.append(unsafe)
                    continue
                if not path.is_file():
                    continue
                relative = path.relative_to(skill_dir).as_posix()
                destination = f"skills/{skill_name}/{relative}"
                git_relative = path.relative_to(root).as_posix()
                raw = _read_blob(root, commit, git_relative)
                found = gate(destination, raw.decode("utf-8", errors="replace"))
                if found:
                    result.problems.extend(found)
                    continue
                result.candidates.append((destination, raw, plugin, skill_name))
        result.skills_by_plugin[plugin] = skill_names
    return result


def apply_plan(result, *, repo_url, commit, marketplace_label, audiences,
                harness_dir=None, manifest_path=None, provenance_path=None, now=None):
    """Write the plan's candidates, update the manifest and provenance file. Refuses to run
    against a plan with unresolved problems or collisions."""
    if not result.ok:
        raise SyncError("refusing to write: unresolved packaging-gate problems or "
                         "skill-name collisions")

    harness_dir = harness_dir or HARNESS
    manifest_path = manifest_path or MANIFEST
    provenance_path = provenance_path or PROVENANCE

    manifest = load_manifest(manifest_path)
    entries = {entry["destination"]: entry for entry in manifest["files"]}
    for destination, raw, plugin, skill_name in result.candidates:
        target = harness_dir / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(raw)
        entries[destination] = {
            "category": "skill",
            "source": destination,
            "destination": destination,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "vendor": {
                "marketplace": marketplace_label,
                "repo_url": repo_url,
                "commit": commit,
                "plugin": plugin,
                "plugin_version": result.plugin_versions[plugin],
                "audience": audiences.get(plugin, "standard"),
            },
        }
    manifest["files"] = [entries[key] for key in sorted(entries)]
    manifest["bundle_version"] = int(manifest.get("bundle_version", 1)) + 1
    with open(manifest_path, "wb") as handle:
        handle.write((json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))

    provenance = load_provenance(provenance_path)
    synced_at = now or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    provenance["sources"][marketplace_label] = {
        "repo_url": repo_url,
        "commit": commit,
        "synced_at": synced_at,
        "license": "none",
        "license_note": LICENSE_NOTE,
        "plugins": [
            {
                "name": plugin,
                "version": result.plugin_versions[plugin],
                "audience": audiences.get(plugin, "standard"),
                "skills": sorted(result.skills_by_plugin[plugin]),
            }
            for plugin in sorted(result.skills_by_plugin)
        ],
    }
    provenance["sources"] = {key: provenance["sources"][key] for key in sorted(provenance["sources"])}
    with open(provenance_path, "wb") as handle:
        handle.write((json.dumps(provenance, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))

    return manifest["bundle_version"]


def _parse_kv_pairs(pairs):
    result = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key or not value:
            raise SyncError(f"expected plugin=value, got {pair!r}")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True,
                         help="Local git checkout path, or a git URL to clone.")
    parser.add_argument("--commit", required=True, help="Pinned 40-hex commit SHA.")
    parser.add_argument("--repo-url", required=True,
                         help="Canonical origin URL, recorded in provenance.")
    parser.add_argument("--plugin", action="append", dest="plugins", required=True,
                         help="Plugin name to vendor; repeatable (the allowlist).")
    parser.add_argument("--audience", action="append", dest="audiences", default=[],
                         help="plugin=audience, e.g. uifw=technical. Default: standard.")
    parser.add_argument("--marketplace", default=None,
                         help="Provenance key; defaults to --repo-url.")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: plan only).")
    args = parser.parse_args(argv)

    try:
        root = resolve_source(args.source, args.commit)
        result = plan(root, args.plugins, commit=args.commit)
        audiences = _parse_kv_pairs(args.audiences)
    except SyncError as exc:
        print(f"error: {exc}")
        return 2

    for plugin in sorted(result.skills_by_plugin):
        for skill in result.skills_by_plugin[plugin]:
            print(f"  + {plugin}: skills/{skill}")

    if result.collisions:
        print()
        print("BLOCKED: name collision with a skill this plugin does not already own:")
        for name in sorted(set(result.collisions)):
            print(f"  {name}")

    if result.problems:
        print()
        print(f"BLOCKED by the packaging gate ({len(set(result.problems))} problems):")
        for problem in sorted(set(result.problems))[:30]:
            print(f"  {problem}")

    if not result.ok:
        print()
        print("Nothing was written.")
        return 2

    if not args.apply:
        print("(dry run: pass --apply to write)")
        return 0

    marketplace_label = args.marketplace or args.repo_url
    bundle_version = apply_plan(
        result, repo_url=args.repo_url, commit=args.commit,
        marketplace_label=marketplace_label, audiences=audiences,
    )
    vendored = sum(len(skills) for skills in result.skills_by_plugin.values())
    print(f"written: bundle_version {bundle_version}, {vendored} skills vendored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
