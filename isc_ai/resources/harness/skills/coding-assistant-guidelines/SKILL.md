---
name: coding-assistant-guidelines
description: "Trigger: generated code, scripts, config, docs. Require human ownership and verification."
license: MIT
metadata:
  author: isc-ai
  version: "1.0"
---

## Hard Rules
- The human owns correctness, architecture, security, and maintainability.
- Never treat generated output as finished or human accepted.
- Use minimum privilege and request approval before system, network, credential, or elevated actions.
- Never place proprietary or sensitive data in external prompts.

## Execution Steps
Compile, test behavior and edge cases, verify privilege scope, and keep human
review separate from automated evidence.

## Output Contract
Report checks, privilege used, approvals, review status, and remaining gaps.
