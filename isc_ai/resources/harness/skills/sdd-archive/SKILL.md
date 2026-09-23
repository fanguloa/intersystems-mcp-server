---
name: sdd-archive
description: "Trigger: SDD archive. Close a verified change and preserve lineage."
license: MIT
metadata:
  author: isc-ai
  version: "1.0"
---

## Activation Contract
Archive only after verification is complete.

## Hard Rules
- Refuse reports with CRITICAL findings.
- Merge deltas before moving OpenSpec artifacts.
- Preserve unrelated requirements and never modify archived changes.
- Record all Engram observation IDs in the archive report.

## Execution Steps
Retrieve all artifacts, synchronize full specs for file modes, archive with an ISO
date, verify contents, and persist `sdd/{change}/archive-report`.

## Output Contract
Return synced domains, archive location, lineage, risks, and the standard envelope.
