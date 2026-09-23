"""Add the InterSystems doctrine skills to the packaged bundle, gate first.

Separate from pack_harness.py on purpose: that tool renders model roles into files that
are already packaged, this one decides whether new files may join the bundle at all. The
gate is the same and it is the point: a file that names a client, a credential or someone's
home directory does not ship.

Usage (from the repository root):
    python tools/add_doctrine_skills.py            # dry run
    python tools/add_doctrine_skills.py --apply
"""

import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bundle_gate import gate

REPO = pathlib.Path(__file__).resolve().parent.parent
HARNESS = REPO / "isc_ai" / "resources" / "harness"
MANIFEST = HARNESS / "manifest.json"
REFERENCE = pathlib.Path.home() / ".claude" / "skills"

SKILLS = ("intersystems-development", "ccr-alignment", "review-changelist")


def main():
    apply_changes = "--apply" in sys.argv
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    known = {entry["destination"] for entry in manifest["files"]}

    candidates, problems = [], []
    for skill in SKILLS:
        root = REFERENCE / skill
        if not root.is_dir():
            print(f"missing from the reference installation: {skill}")
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            destination = f"skills/{skill}/{path.relative_to(root).as_posix()}"
            raw = path.read_bytes()
            found = gate(destination, raw.decode("utf-8", errors="replace"))
            if found:
                problems.extend(found)
                continue
            candidates.append((destination, raw, destination in known))

    print(f"files considered: {len(candidates) + len({item.split(':')[0] for item in problems})}")
    print(f"packable: {len(candidates)}  already in the bundle: {sum(1 for _, _, k in candidates if k)}")
    if problems:
        print()
        print(f"BLOCKED by the packaging gate ({len(set(problems))} problems):")
        for problem in sorted(set(problems))[:30]:
            print(f"  {problem}")
        print()
        print("Nothing was written. Genericize the source files first.")
        return 2

    for destination, _, _ in candidates:
        print(f"  + {destination}")
    if not apply_changes:
        print("(dry run: pass --apply to write)")
        return 0

    entries = {entry["destination"]: entry for entry in manifest["files"]}
    for destination, raw, _ in candidates:
        target = HARNESS / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(raw)
        entries[destination] = {"category": "skill", "source": destination,
                                "destination": destination,
                                "sha256": hashlib.sha256(raw).hexdigest()}
    manifest["files"] = [entries[key] for key in sorted(entries)]
    manifest["bundle_version"] = int(manifest.get("bundle_version", 1)) + 1
    with open(MANIFEST, "wb") as handle:
        handle.write((json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"written: bundle_version {manifest['bundle_version']}, {len(manifest['files'])} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
