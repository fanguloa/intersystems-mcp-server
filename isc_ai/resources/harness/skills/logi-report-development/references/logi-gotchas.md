# Logi Report 24.1 / TrakCare T2026 gotchas (verified in a site BASE)

## Runtime
- Logi renders `.cls.xml` and `.cat.xml` directly; reference subreports as `X.cls.xml`. `Report.JReport.ChangeControl.ConvertToBinary` was observed failing (log4j cannot create its log): do not depend on binaries.
- Logi Server error log: `/opt/JReport/Server/logs/Error.log`. Read the "Caused by": SQLCODE -99 means a missing grant; date parameters use `dd/MM/yyyy`.
- TrakCare overrides the JDBC URL and credentials and injects `X_SESS_USERID`, `X_SESS_CTLOCID` and other session values. Catalogs carry no credentials.
- Logi connects as `TrakCareJReportUser` (role `TrakCareSQLUserRole`). SQL grants do not travel in the ItemSet; the deployer `Deploy()` applies them. `$SYSTEM.SQL.Security.CheckPrivilege` throws `<UNDEFINED>` for a procedure that does not exist. `GRANT EXECUTE ON <schema.proc> TO <role>` (no `PROCEDURE` keyword) returns SQLCODE 100 when already granted.
- An "Evaluation Copy" watermark comes from the Logi Server license, not from the report: report it as infrastructure.
- A logo URL that points to another environment can hang the render; resolve it to the local web image file.

## Report XML
- `QueryDataSet` needs `<dsName>` equal to the catalog database name; otherwise Logi looks in "Data Source 1".
- `AutoFit` adjusts width and height: use `AutoFit` + `MaxWidth` so a field only grows down.
- Row separators: bottom border of the panel, never a line at a fixed Y (it does not move when text grows).
- A `DetailPanel` is not split across pages (Cross Page defaults to false): keep a heading in the same panel as its first content row.
- Table headers of a subreport go in `BandedHeader`; a `BandedPageHeader` is not printed inside a subreport.
- Footer at the bottom of the last page (product pattern): `BandedPageFooter` with `SuppressInLastPage`, plus `BandedFooter` with `ToBottom` and the same subreport. Set `TopLine/LeftLine/RightLine/BottomLine=None` or Logi draws a box.
- `ParamSubLink.MainColumn` accepts main dataset columns (their catalog mapping name, e.g. `UNIT1` after a collision).
- Column-bound properties: `<PictureName>@LogoUrl|||0|@</PictureName>`.
- Formulas (`@QFY_`, `formulainfo`) carry compiled bytecode: never generate them.

## TrakCare / IRIS
- `websys.Report` from a runner: `%xmlconfigitem=1` + `%Save`, then `TC.ChangeControl.CCR` `CheckOut` + `ExportElement` and set the CCR on the new `%Studio.SourceControl.Change` row ([recipe](../assets/register-websys-report.mac.txt)). A plain `%Save` fails with "Source Control Credentials are not set".
- The direct preview URL `jreportpreview.csp?ID=..&TREPORT=..&TPRINTSERVER=1&P1=..` passes only the Pn in the URL; `websys.Report` Pn expressions are not evaluated.
- The preview PDF arrives as `application/pdf` from `web.Msg.DocumentPreview.cls`, but `response.body()` returns the browser viewer: replay the request with the session cookies ([helper](../assets/logi-report-render.ts)).
- The server normalizes `Parameter X = "01"` to `01` on save: use literals in code.
- A large deployer class (thousands of lines) can time out in the MCP and still be saved and compiled: verify before retrying.
- `SS_UserDefWindow.WINPrintReportDR` can link a questionnaire to the `websys.Report` (a configuration change of its own).
