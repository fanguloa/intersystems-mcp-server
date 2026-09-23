---
name: review-changelist
description: "Trigger: review changelist, P4 review, changelist review, review CL, review TC- ticket, ObjectScript review. Review Perforce changelists on Windows/IRIS before integration."
license: Apache-2.0
metadata:
  author: gentleman-programming
  version: "1.0"
---

## Activation Contract

Apply when reviewing a Perforce changelist for major issues before integration on Windows, targeting IRIS/ObjectScript (`.cls`) code. Source: AUDEV "Code Review Skill" (P4 `/<workspace-root>/vscode/skills/review-changelist/`).

## Hard Rules

- If no changelist number is given, assume the user's current pending changelist.
- For a JIRA `TC-<Number>`, resolve linked changelists via `p4 fixes -j TC-<Number>` and review each individually.
- Always save patch files under `reviews\cl-<Number>.patch` at the workspace root; create the directory if missing.
- Skip auto-generated noise: `element/config/websys/SysPreferences` and SQL query-plan comments (`-- ;` blocks).
- When a diff calls a method in another class, open that class and verify the parameter signature, skipped-parameter defaults, and documented contract before approving.
- Classify every finding as 🔴 Critical (blocks), 🟡 Standard (requires fix), or 🟢 Observation (non-blocking).

## Decision Gates

| Situation | Action |
|-----------|--------|
| Pending/shelved changelist | `p4 shelve -c <N>`, then `p4 describe -du -S <N>` |
| Submitted changelist | `p4 describe -du <N>` (no `-S`) |
| Diff insufficient for context | Open the class via `isfs://` or MCP `getDoc` (`ClassName.cls`) |
| Cross-class method call changed | Verify signature with `vscode_listCodeUsages` or `getDoc` |
| File is SysPreferences or SQL plan | Ignore — not meaningful |

## Execution Steps

1. Identify the changelist (see `assets/p4-commands.md`).
2. Shelve and generate the patch to `reviews\cl-<N>.patch`.
3. Read the diff; open full classes only when context is missing.
4. Verify cross-class calls and apply the standards checklist (`references/standards-checklist.md`).
5. Record findings by severity and produce the change note (`assets/change-note-template.md`).

## Output Contract

Return: changelist number(s), impacted `.cls` files, findings grouped by 🔴/🟡/🟢 with rationale, and the technical change note. State clearly whether the changelist is safe to integrate.

## References

- `assets/p4-commands.md` — Perforce/IRIS commands and depot→class translation.
- `assets/change-note-template.md` — technical change note format.
- `references/standards-checklist.md` — naming, code, security, database, property standards.
- AUDEV Confluence: "Code Review Skill" (page 1197896587).
