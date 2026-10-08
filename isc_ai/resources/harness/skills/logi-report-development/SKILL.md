---
name: logi-report-development
description: "Trigger: Logi Report, JReport, InterSystems Reports, printable report, websys.Report, .cat.xml, .cls.xml, isc-ai logi. Build TrakCare Logi reports as code, spec to reviewed PDF."
license: Apache-2.0
metadata:
  author: fangulo
  version: "1.0"
---

## Activation Contract

Load when creating or changing a TrakCare Logi (InterSystems Reports) printable: SQL stored procedures, JSON specs, generated catalog/report XML, the deployer class, the `websys.Report` record, per-environment deployment or the review of the rendered PDF. The agent designs the layout against the requirement mockup.

## Hard Rules

- Load [InterSystems development](../intersystems-development/SKILL.md) and the project workflow first. This skill authorizes no environment, deletion or promotion.
- Never hand-edit generated `.cls.xml`, `.cat.xml` or the deployer class: change the spec or the ROWSPEC and run `isc-ai logi`.
- Business logic lives in the stored procedures (site package, one class per area plus shared `Common` classes). Generate no Logi formulas: they carry compiled bytecode.
- Header/footer texts and patient context come from SP columns linked to the component, not from `websys.Report` Pn literals.
- Catalogs carry no credentials (TrakCare injects JDBC URL, user and password). Never reuse keys embedded in product tools.
- Registering `websys.Report` in BASE, deploying in any environment and every promotion need explicit approval.

## Decision Gates

| Need | Action |
|---|---|
| Common data (patient, professional, history, header) | Reuse or extend the site `Common` procedures |
| Block reused by several reports | Component spec (subreport), copied into each area folder |
| New functional area | New catalog in the manifest (folder, deployer class) |
| Unsure how Logi behaves | Copy the XML structure of a product report and check [gotchas](references/logi-gotchas.md) before inventing |

## Execution Steps

1. Map every field of the mockup to its source with read-only SQL.
2. Write the procedures; test them as table functions with a test record (an unknown id returns 0 rows).
3. Write the spec ([reference](references/spec-reference.md)) and list it in the manifest.
4. `isc-ai logi --manifest <manifest>`, then the same with `--check` (exit 0).
5. Save and compile the procedures and the deployer under the CCR (`iris_save_class_from_file`, `ccr_id`, `compile=true`). On a timeout of the large deployer, verify XData and compile time before retrying.
6. In BASE run `Deploy()` (writes files, checks SHA-256, grants EXECUTE); `Verify()` only checks.
7. Register the `websys.Report` once in BASE with [the recipe](assets/register-websys-report.mac.txt).
8. Render through TrakCare with [the Playwright helper](assets/logi-report-render.ts), read the PDF, compare it with the mockup and iterate from step 2 or 3.
9. CCR Implementation Plan per environment: load the ItemSet, then `Deploy()`.

## Output Contract

Report new procedures/columns, generated files, `Deploy()`/`Verify()` result, PDF path and page count, deviations from the mockup and pending functional decisions (one question at a time).

## References

- [Spec reference](references/spec-reference.md), [Logi/TrakCare gotchas](references/logi-gotchas.md)
- [websys.Report recipe](assets/register-websys-report.mac.txt), [Playwright render helper](assets/logi-report-render.ts)
