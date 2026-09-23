# Documentation evidence and ObjectScript conventions

Use official **InterSystems** material first for ObjectScript/API questions. Resolve the Context7 library **in the current session before querying**, as required by the tool contract, even when a previously verified ID is recorded here.

| Source | Scope / evidence status |
| --- | --- |
| `/websites/intersystems_irislatest` | Official IRIS documentation collection; ID verified in the parent remediation research |
| `/websites/intersystems_irislatest_csp_documatic_25csp_documatic_cls` | Official class reference collection; ID verified in the parent remediation research |
| `docs.intersystems.com` version-specific manuals and class reference | Check against actual deployed product/version; `irislatest` does **not** establish installed-version compatibility |
| Authorized private TrakCare documentation or exact source revision | Needed for private business APIs; generic IRIS docs do not prove those contracts |
| Existing deployment workflow and skill examples | Historical local evidence, not authoritative language specifications |

Public Context7 lookup was reported working in the parent session. This offline writing unit made no public lookup and verified no installed IRIS version. Account/key/OAuth ownership and duplicate Context7 configuration cleanup remain pending. Do not put secrets, customer code or business records in documentation queries. Query one narrowly scoped API/concept at a time; record product/version, source and compatibility limits.

## Code guidance, subject to version verification

- Prefer explicit `{ ... }` conditional blocks to avoid line-scoping ambiguity. This is a project-readable style, not a claim that every single-line conditional is invalid.
- Distinguish method return from loop exit. Check `RETURN`/`QUIT` legality in the actual routine/block context and compiler version before applying a pattern; do not rewrite all exits mechanically.
- Verify `IF`/`ELSEIF`/`ELSE` syntax in the relevant language reference. The old blanket assertion that a new-line `Else` is always incorrect is withdrawn.
- Verify native JSON APIs (`%DynamicObject`, `%DynamicArray`, `%FromJSON`, `%Set`, `%GetIterator`) and dynamic-expression syntax before using them; preserve data types and error handling.
- Prefer documented dictionary APIs and permitted namespace access. Reads of internal `oddCOM`/`oddDEF` structures are version-coupled implementation details, not a universal safe metadata API or a security workaround. Existing uses require scoped review; no automatic migration is authorized here.
- Validate source encoding, XML serialization/escaping and transport round trip separately. Unicode is not inherently an XML error; a SAX failure needs its actual byte/codepoint/encoding cause diagnosed. Preserve business strings and report only safe diagnostic locations. Keep unresolved packaging blocked rather than normalizing away data.

No syntax/API compatibility or deployment acceptance is claimed by these policy edits.
