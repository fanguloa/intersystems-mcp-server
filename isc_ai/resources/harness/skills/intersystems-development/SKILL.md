---
name: intersystems-development
description: "Trigger: ObjectScript, IRIS, TrakCare, CCR, .cls, .mac, .inc. Apply shared development, discovery and package gates."
license: Apache-2.0
metadata:
  author: fangulo
  version: "1.0"
---

## Activation Contract

Load for InterSystems development, review, diagnosis or delivery, together with [AI work policy](../ai-work-policy/SKILL.md) and the project workflow. Read the [environment/package matrix](references/environment-package-policy.md) before selecting a write route.

## Hard Rules

- Autodiscover CCR applicability for the **actual canonical instance + namespace**. Record installed engine, configured binding and discovery completeness separately. Suspension is **process-local**: revalidate it in the actual execution process before mutation, never through an unrelated request or namespace-cached authorization. A product/layer name is not evidence.
- Treat suspended, failed, incomplete or mismatched discovery as blocked for writes; never as absence of CCR. Never disable control to obtain a direct-write route.
- Intersect package, environment, operation and current approval gates. BASE is the origin; UAT/TEST require the applicable sanctioned route; LIVE/PROD writes are prohibited. Override environment variables do not authorize them.
- Preserve a local candidate tied to the observed deployed revision. Review the exact diff and retain rollback evidence; never bypass it with inline server edits.
- Preserve Unicode business strings and comments. Validate source/transport/XML encoding and round-trip content. Report failures and stop packaging if unresolved; never silently strip or transliterate.
- Verify APIs and syntax against official documentation for the deployed version. Use explicit conditional blocks as a readability convention; do not claim `Else` must universally share a line. Do not prescribe internal metadata globals or namespace switching as universally safe.

## Decision Gates

| Evidence | Action |
| --- | --- |
| Matching configured CCR, active in the actual execution process | Use native tracked change/promotion route after process-local suspension revalidation |
| CCR demonstrably absent | Apply explicit non-CCR environment/package gates; absence alone grants nothing |
| Suspended / unknown / conflicting | Stop writes; obtain authorized diagnostic evidence |
| Core or undocumented API required | Prefer supported custom/configuration paths; require exact scoped exception and version evidence |

## Execution Steps

1. Confirm target, network, deployed product version and approved scope.
2. Resolve official InterSystems documentation per session; use narrow, non-sensitive queries.
3. Discover CCR and apply the global matrix plus project topology.
4. Edit/review locally; validate with permitted checks. Deploy only when separately authorized and applicable gates pass.

## Output Contract

Report target/version, discovery evidence and state, permitted route, local revision/diff, encoding checks and review/test/acceptance status. Unknowns remain explicit blockers.

## References

- [Environment, package and exception policy](references/environment-package-policy.md)
- [Documentation sources and code conventions](references/documentation-sources.md)
