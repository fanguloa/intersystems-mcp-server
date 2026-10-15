# Logi Report 24.1 / TrakCare T2026 gotchas (verified in a site BASE)

## Runtime
- Logi renders `.cls.xml` and `.cat.xml` directly (subreports referenced as `X.cls.xml`), but stamps them with the evaluation watermark (see below). `Report.JReport.ChangeControl.ConvertToBinary` was observed failing (log4j cannot create its log): Logi's logger creates `LogiReport_RF.log` in the current directory, and `$ZF(-100)` runs in the IRIS data directory, where the IRIS OS user may not write. Running the `Convert` script from a writable directory produces the binary (`FA CE FA DE` header, same as product): the generated `Deploy()` does it (`Convert()` repeats only that step); [the conversion recipe](../assets/convert-logi-binary.mac.txt) is the manual fallback. Its exit code is not reliable: judge by a fresh binary with that header. Binaries keep the subreport and catalog names written in the XML (`X.cls.xml`); verified that this still renders without watermark.
- Logi Server error log: `/opt/JReport/Server/logs/Error.log`. Read the "Caused by": SQLCODE -99 means a missing grant; date parameters use `dd/MM/yyyy`.
- TrakCare overrides the JDBC URL and credentials and injects `X_SESS_USERID`, `X_SESS_CTLOCID` and other session values. Catalogs carry no credentials.
- Logi connects as `TrakCareJReportUser` (role `TrakCareSQLUserRole`). SQL grants do not travel in the ItemSet; the deployer `Deploy()` applies them. `$SYSTEM.SQL.Security.CheckPrivilege` throws `<UNDEFINED>` for a procedure that does not exist. `GRANT EXECUTE ON <schema.proc> TO <role>` (no `PROCEDURE` keyword) returns SQLCODE 100 when already granted.
- "Logi Report Evaluation Copy" watermark: verified on a site BASE that product binary `.cls`/`.cat` render clean while generated `.cls.xml`/`.cat.xml` on the same server are stamped. Converting the main report and catalog to binary removes it from the whole PDF, even with subreports still referenced as `X.cls.xml`. Do not blame the Server or Designer license first: render a product report on the same server to compare, and deliver binaries produced by the TrakCare converter.
- A logo URL that points to another environment can hang the render; resolve it to the local web image file.

## Report XML
- `QueryDataSet` needs `<dsName>` equal to the catalog database name; otherwise Logi looks in "Data Source 1".
- `AutoFit` adjusts width and height: use `AutoFit` + `MaxWidth` so a field only grows down.
- Row separators: bottom border of the panel, never a line at a fixed Y (it does not move when text grows).
- A `DetailPanel` is not split across pages (Cross Page defaults to false): keep a heading in the same panel as its first content row. A panel holding a long subreport moves whole to the next page and leaves the previous one blank: set its XML property `CanCrossPage` to `true` (spec `crossPage`). Property names are in the Server help, `help/userguide/designer/content/html/appendix/apdx_param.htm`.
- Table headers of a subreport go in `BandedHeader`; a `BandedPageHeader` is not printed inside a subreport.
- Footer at the bottom of the last page (product pattern): `BandedPageFooter` with `SuppressInLastPage`, plus `BandedFooter` with `ToBottom` and the same subreport. Set `TopLine/LeftLine/RightLine/BottomLine=None` or Logi draws a box.
- `ParamSubLink.MainColumn` accepts main dataset columns (their catalog mapping name, e.g. `UNIT1` after a collision).
- Column-bound properties: `<PictureName>@LogoUrl|||0|@</PictureName>`.
- Proportional image: `Constrain_Proportion=true` and NO `ScalingMode`. `ScalingMode=Customize` stretches the picture to its box even with `Constrain_Proportion` (a 1.76 logo was drawn at 2.31). Check it in the PDF: the `cm` matrix before `/img Do` gives the drawn width and height.
- Formulas (`@QFY_`, `formulainfo`) carry compiled bytecode: never generate them.

## TrakCare / IRIS
- `websys.Report` from a runner: `%xmlconfigitem=1` + `%Save`, then `TC.ChangeControl.CCR` `CheckOut` + `ExportElement` and set the CCR on the new `%Studio.SourceControl.Change` row ([recipe](../assets/register-websys-report.mac.txt)). A plain `%Save` fails with "Source Control Credentials are not set".
- Build the change-control OID with the id as a STRING: `$ListBuild("826", "websys.Report")`. A numeric literal (`$ListBuild(826, ...)`) is another `^OBJ.GUID` key: `ExportElement` assigns a NEW GUID ("Assigned GUID ...") and writes a second XML file in the Perforce workspace, while the real GUID file keeps stale values. Undo: kill `^OBJ.GUID(0,<newguid>)` and `^OBJ.GUID(1,$lb(826,"websys.Report"))`, delete the stray file and export again with the string id.
- The direct preview URL `jreportpreview.csp?ID=..&TREPORT=..&TPRINTSERVER=1&P1=..` passes only the Pn in the URL; `websys.Report` Pn expressions are not evaluated.
- The preview PDF arrives as `application/pdf` from `web.Msg.DocumentPreview.cls`, but `response.body()` returns the browser viewer: replay the request with the session cookies ([helper](../assets/logi-report-render.ts)).
- The server normalizes `Parameter X = "01"` to `01` on save: use literals in code.
- A large deployer class (thousands of lines) can time out in the MCP and still be saved and compiled: verify before retrying.
- `SS_UserDefWindow.WINPrintReportDR` can link a questionnaire to the `websys.Report` (a configuration change of its own).
