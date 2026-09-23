# Global InterSystems change policy

This is the shared authority for CCR behavior and allowed packages. Project workflows provide topology and narrower business constraints, not alternative permission rules. All routes remain bounded by current user approval and harness permissions.

## Discovery record and decision

Key applicability evidence by canonical instance identity **and namespace**, not server alias, product, path prefix or layer. Record target/VIP, environment, product/version, observation time, configured source-control class and TC binding, installed matching engine, read permissions/errors and completeness. Reconfirm after target/configuration changes and before a write. Never reuse another namespace's result. Suspension is a separate **process-local operational gate**, revalidated in the actual mutation process; an unrelated metadata request cannot establish it or authorize a later write.

| Discovery state | Required evidence | Route |
| --- | --- | --- |
| `controlled-active` | Namespace applicability established by installed engine and matching configured binding; suspension explicitly false in the actual execution process | CCR/source-control route after process-local revalidation |
| `controlled-suspended` | Matching installed/configured engine, suspension true in the actual execution process | Block writes; diagnose; never direct-edit fallback |
| `absent` | Successful, sufficient checks establish no configured applicable control engine, no applicable installed engine and no conflicting evidence | Evaluate explicit non-CCR exception; not automatic permission |
| `unknown` | Tool unavailable, timeout, denied/missing fields, unsupported engine, installed-only/configured-only/mismatch, uncertain suspension or identity | Block writes; resolve discovery |

For TrakCare control, inspect namespace source-control configuration, the separate TC binding and matching `TC.ChangeControl.Interface`/CCR engine availability using a read-only discovery facility. A single boolean is insufficient. `TC/ChangeControl/Interface.xml` (local deployment reference, lines 25–36 and 883–922) distinguishes namespace binding from `%zSuspendLevel`, a process-local variable. Never query private globals or create an unrelated runner to claim operational suspension evidence. Do not infer “TrakCare means CCR” or “INT means no CCR.” Full enforcement requires an authorized backend facility that revalidates binding and suspension in the mutation process; partial discovery must leave unavailable evidence UNKNOWN and block controlled-mode activation.

## Environment and operation gates

| Environment | Policy |
| --- | --- |
| BASE | Origin of changes. Matching active CCR requires tracked checkout/save and approved bundle/upload. Confirmed absence permits only cataloged own-package work under approved scope |
| UAT / TEST, controlled-active | Approved central ItemSet promotion only; no arbitrary local-file load or direct code edits |
| UAT, absent | Direct write only where an exact existing project exception below applies, with explicit approval and all preservation gates |
| TEST / QA / other unconfigured targets | Block until canonical topology, package scope and route are explicitly configured; do not extrapolate UAT exceptions |
| LIVE / PROD | No writes or promotion. An override such as `IRIS_ALLOW_PROD=1` is never authorization |
| Any suspended / unknown target | Block writes, including compilation, checkout and persistent execution |

Read-only diagnosis still requires permitted tools/network/data scope. Arbitrary execution, deletion, CCR upload/promotion, shared-package changes and any allowed UAT direct write are gated by explicit approval. Perforce MCP is read-only; product delivery uses the approved CCR/Perforce process, never an ad hoc submit. Cleanup is a write and inherits these gates.

## Allowed-package catalog

No wildcard is implicitly granted by examples, by the existence of source files, or by a
nested name. Unknown packages block. Adding one requires explicit human confirmation and
a catalog update in the deployment that owns it.

The catalog is per deployment and lives with the deployment, not in this shared policy:

| Where | What it holds |
| --- | --- |
| `<deployment>/.iris-mcp/config.json`, `packages.<namespace>` | The effective catalog, enforced by code on every write. `isc-ai docs` renders it into the deployment workflow and `isc-ai audit` reports drift |
| `<deployment>/.agents/skills/.../references/package-catalog.md` | The provenance the policy file cannot hold: why each prefix is allowed, when it was approved, under which CCR, and the extra gate that applies to it |

Rules that hold for every deployment, whatever its catalog says:

- A grant is scoped to a namespace and a role. It never extends to another namespace,
  another environment or a sibling package by analogy.
- An exact-class grant grants that class only: no sibling, no package wildcard, no
  alternate document type.
- Shared code under a cross-project prefix is never covered by an own-package allowance.
  It needs strong justification, peer review and explicit human permission per target.
- Nested names such as `Custom.<Site>.…web.…` remain custom and stay subject to the
  own-package scope; a nested segment is not a core exemption.
- Core IRIS/TrakCare (`%*`, `Ens.*`, standalone `web.*`, `model.*`, `epr.*`) and
  `Region.*` are read-only: no edit, checkout, compile or inclusion in a CCR. Stop and
  look for a supported custom or configuration solution. A core exception exists only
  where the deployment catalog records one explicitly.
- A configured CT-record change is not permission to edit its core class: use the
  supported configuration/CCR mechanism with exact record scope and approval.
- Historical core-stub restoration, GUID repairs or source-control record fixes are
  incident evidence, not standing permission.
- An environment variable that widens the catalog at runtime (for example
  `IRIS_EXTRA_PACKAGES`) is a convenience for an already approved grant, never the
  approval itself.

## Preservation workflow for permitted INT delivery

Discover first. Fetch the **destination** production, bind a local candidate to its observed revision, take an immutable timestamped backup and a safe `Ens.Config.DefaultSettings` snapshot, merge only approved Items/settings, review exact diff and rollback plan, then use the route selected by discovery. Preserve UAT-only components and environment endpoints. Recheck destination drift immediately before delivery; if changed, reconcile rather than overwrite. Verify compilation, production/components and preserved settings with separately permitted checks. This protocol never grants an unlisted package or TEST/QA target.
