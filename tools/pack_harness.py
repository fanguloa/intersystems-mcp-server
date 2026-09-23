"""Maintainer tool: render the sub-agent model roles into the packaged harness.

The model assignment used to be a hand edit in the installed copies, which made every
packaged file differ from what is deployed and left the whole bundle in `conflict`,
unupdatable. Here it becomes data: the role template is the source, the packaged agent
frontmatter is the render, and the manifest digests are refreshed so the bundle stays
verifiable byte for byte.

What this tool deliberately does NOT do: copy files from a local installation. The
installed copies are not authoritative -- some are older versions from an earlier
harness -- so importing them silently regresses reviewed content.

Usage (from the repository root):
    python tools/pack_harness.py                # dry run
    python tools/pack_harness.py --apply
    python tools/pack_harness.py --apply --model sdd-apply=opus
"""

import hashlib
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
HARNESS = REPO / "isc_ai" / "resources" / "harness"
MANIFEST = HARNESS / "manifest.json"
TEMPLATE = HARNESS / "skills" / "ai-work-policy" / "assets" / "model-role-template.json"

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
MODEL_LINE = re.compile(r"(?m)^model:[^\n]*\n")


def families(template):
    """role -> model family, resolved through the profile table.

    A preference may name a profile (`sonnet-preference`) or already be the family
    itself, which is what it becomes after the first render. Both resolve, so the tool
    is idempotent instead of finding nothing on a second run.
    """
    profiles = template.get("profiles") or {}
    resolved = {}
    for role, preference in (template.get("role_defaults") or {}).items():
        profile = profiles.get(preference)
        if profile is None:
            resolved[role] = preference
            continue
        family = profile.get("model_id") or profile.get("intended_family")
        if family:
            resolved[role] = family
    return resolved


def render(text, model):
    """Set `model:` in the frontmatter, after `name:` when there is one."""
    match = FRONTMATTER.match(text)
    if not match:
        raise SystemExit("a packaged agent must start with a frontmatter block")
    block = MODEL_LINE.sub("", match.group(1) + "\n")
    lines = block.rstrip("\n").split("\n")
    insert_at = next((index + 1 for index, line in enumerate(lines)
                      if line.startswith(("name:", "description:"))), len(lines))
    lines.insert(insert_at, f"model: {model}")
    return "---\n" + "\n".join(lines) + "\n---\n" + text[match.end():]


def main():
    apply_changes = "--apply" in sys.argv
    overrides = {}
    for argument in sys.argv:
        if argument.startswith("--model") and "=" in argument:
            role, _, model = argument.partition("=")
            overrides[role.split(" ")[-1].lstrip("-")] = model
    for index, argument in enumerate(sys.argv):
        if argument == "--model" and index + 1 < len(sys.argv) and "=" in sys.argv[index + 1]:
            role, _, model = sys.argv[index + 1].partition("=")
            overrides[role] = model

    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    roles = families(template)
    roles.update(overrides)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    planned = []
    for entry in manifest["files"]:
        destination = entry["destination"]
        if entry["category"] != "agent":
            continue
        role = pathlib.PurePosixPath(destination).stem
        model = roles.get(role) or roles.get("default")
        if not model:
            print(f"no model role for {role}, skipping")
            continue
        path = HARNESS / destination
        current = path.read_text(encoding="utf-8")
        updated = render(current, model)
        if updated == current:
            print(f"  {destination:34} already declares model: {model}")
            continue
        planned.append((entry, path, updated, model))
        print(f"  {destination:34} model: {model}")

    if not planned:
        print("nothing to render")
        return 0

    if template.get("runtime_applied") is not True:
        print()
        print("the role template still says runtime_applied=false; it is the source now, "
              "so the flag is flipped as part of this render")

    if not apply_changes:
        print("(dry run: pass --apply to write)")
        return 0

    for entry, path, updated, _ in planned:
        # Newline style is fixed: the digests are computed over these exact bytes and
        # .gitattributes keeps git from translating them.
        with open(path, "wb") as handle:
            handle.write(updated.encode("utf-8"))
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    template["runtime_applied"] = True
    template["role_defaults"] = {role: roles.get(role, roles.get("default"))
                                 for role in (template.get("role_defaults") or {})}
    template_bytes = (json.dumps(template, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    with open(TEMPLATE, "wb") as handle:
        handle.write(template_bytes)
    for entry in manifest["files"]:
        if entry["destination"].endswith("model-role-template.json"):
            entry["sha256"] = hashlib.sha256(template_bytes).hexdigest()

    manifest["bundle_version"] = int(manifest.get("bundle_version", 1)) + 1
    with open(MANIFEST, "wb") as handle:
        handle.write((json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"written: bundle_version {manifest['bundle_version']}, {len(planned)} agents rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
