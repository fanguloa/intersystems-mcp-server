---
name: ai-work-policy
description: "Trigger: AI work, permissions, review, model routing. Apply human-led evidence policy."
license: MIT
metadata:
  author: isc-ai
  version: "1.0"
---

## Activation Contract
Apply to assistant work before project-specific instructions.

## Hard Rules
- Runtime permissions and explicit user scope override automation.
- Keep sensitive data out of external prompts and persistent artifacts.
- Treat generated output as a candidate requiring tests and human review.
- Never invent model availability, a selector, delegation, or acceptance evidence.

## Execution Steps
Establish scope, make the smallest reviewable change, run permitted checks, seek
fresh review for functional code, and report blocked evidence distinctly.

## Output Contract
Report changes, checks, review status, human acceptance status, and runtime gaps.

## References
- [references/runtime-compatibility.md](references/runtime-compatibility.md)
- [assets/model-role-template.json](assets/model-role-template.json)
