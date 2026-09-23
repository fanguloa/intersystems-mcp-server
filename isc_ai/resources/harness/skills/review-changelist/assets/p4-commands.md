# Perforce / IRIS Commands

## Identify the changelist

```powershell
# Most recent pending changelist for the current user
p4 changes -s pending -u $env:USERNAME -m 1

# All pending changelists for the current user
p4 changes -s pending -u $env:USERNAME

# Changelists linked to a JIRA/job (TC-<Number>)
p4 fixes -j TC-<Number>
```

Use the returned number as `<Number>` in all later steps.

## Generate the patch

```powershell
# Shelve the changelist
p4 shelve -c <Number>

# Ensure the reviews directory exists
$null = New-Item -ItemType Directory -Force -Path reviews

# Pending/shelved changelist (-S diffs shelved files)
p4 describe -du -S <Number> > reviews\cl-<Number>.patch

# Submitted changelist (no -S flag)
p4 describe -du <Number> > reviews\cl-<Number>.patch
```

Patch files always live under `reviews\` at the workspace root (e.g. `reviews\cl-8894299.patch`).

## Other useful commands

```powershell
p4 opened -c <Number>        # List files in the changelist
p4 describe -du <Number>     # Diff of a submitted changelist
p4 diff -du <File>           # Diff of a specific file
```

## Depot path → IRIS class name

1. Take the path segment after `cls/`.
2. Replace `/` with `.`.
3. Replace `.xml` with `.cls`.

| Depot path | IRIS class |
|---|---|
| `//trak/main/DEV/cls/web/LBCTestSetRevisionDefaultAliquot.xml` | `web.LBCTestSetRevisionDefaultAliquot.cls` |
| `//trak/main/DEV/cls/websys/JS/LBSpecimenContainer/Find.xml` | `websys.JS.LBSpecimenContainer.Find.cls` |

Open via the `isfs://` virtual filesystem (e.g. `isfs://irishealth:ent-dev/web/LBCTestSetRevisionDefaultAliquot.cls`) or the MCP `getDoc` tool passing `ClassName.cls`.

## Verifying a cross-class method call

```objectscript
// Diff shows:
- set sc = ##class(web.LBEpisode).FinaliseLabEpisode(LBEpisodeID)
+ set sc = ##class(web.LBEpisode).FinaliseLabEpisode(LBEpisodeID, , 1)

// Open web.LBEpisode.cls and find:
Method FinaliseLabEpisode(LBEpisodeID As %String, Flag As %Boolean = 0, SharingMode As %Integer = 0) As %Status

// Confirms: second arg intentionally skipped (default 0), third arg set to 1 — valid call.
```

Steps: identify the class from the call → jump to the definition with `vscode_listCodeUsages` or `getDoc` → check the parameter list, `[ Private ]` / `[ Internal ]` keywords, and the `///` documentation.
