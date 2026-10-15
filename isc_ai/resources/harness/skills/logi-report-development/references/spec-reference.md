# isc-ai logi: manifest and spec reference

The specs are the source of truth of the layout. Columns and parameters are NOT typed here: they come from the `ROWSPEC` and the formal parameters of each `Query` in the ObjectScript source (`sourceRoot`). An unknown column aborts the generation with `contract violation` and nothing is written.

## manifest.json

```json
{
  "sourceRoot": "../src",
  "outputRoot": "../web/custom/<SITE>/jreports",
  "components": {"HeaderFooter": "specs/components/HeaderFooter.json"},
  "catalogs": [{
    "name": "Inpatient", "folder": "inpatient",
    "deployerClass": "<Site>.Reports.LOGI.Deploy.Inpatient",
    "database": "Data Source 1",
    "jdbcUrl": "jdbc:IRIS://localhost:1972/<NAMESPACE>",
    "parameters": ["X_SESS_CTLOCID", "X_SESS_USERID"],
    "reports": ["specs/inpatient/NursingRecord.json"]
  }]
}
```

- One catalog per area folder (product pattern). Used components are copied into every folder.
- `parameters`: extra catalog parameters (TrakCare injects the `X_SESS_*` session values).
- Optional: `sqlRole` (default `TrakCareSQLUserRole`), `grantMethod` (site helper `Package.Class.Method(procedure, role)`; default plain `GRANT EXECUTE`), `theme` (palette overrides: `primary`, `primaryText`, `accent`, `soft`, `muted`, `rule`, `label`, `text`, `faint`), `formulaSeed` (keep the bytes of catalogs generated with another seed), `convertToBinary` (default `true`: `Deploy()` converts the files to binary; `false` only for a site without the TrakCare converter, and then Logi stamps the evaluation watermark).

## Main report spec

```json
{
  "name": "NursingRecord",
  "source": {"class": "<Site>.Reports.LOGI.Inpatient.spNursingRecord", "query": "Header"},
  "page": {"size": "A4", "orientation": "Portrait", "margin": 0.39},
  "extraParameters": ["X_SESS_CTLOCID", "X_SESS_USERID"],
  "pageHeader": {"component": "HeaderFooter", "tab": 0, "height": 0.86,
                 "links": {"X_SESS_CTLOCID": "pLogonLocID", "ReportTitle": "pReportTitle"}},
  "pageFooter": {"component": "HeaderFooter", "tab": 1, "height": 0.26, "links": {}},
  "blocks": []
}
```

`links`: `{source: subreport parameter}`; the source is a main dataset column or a parameter.

`recordPerPage: true`: one document per dataset row (e.g. a batch of orders printed together); the first body panel of every row starts a new page (Logi `OnNewPage`). Page header/footer links should then use parameters or columns that are equal in every row.

## Blocks (stacked in order)

| type | Fields | Notes |
|---|---|---|
| `band` / `subband` / `title` | `text` | Drawn inside the next block's panel, so never orphaned at a page end |
| `grid` | `columns`, `labelWidth`, `cells: [[label, column or null], ...]`, `after` | One panel per row; long values grow down |
| `textbox` | `column`, `label?`, `height`, `after` | Long text on a soft background |
| `component` | `component`, `tab?`, `links`, `height`, `after`, `crossPage?` | Subreport (`X.cls.xml`). `crossPage: true` lets a long subreport split at the page break (Logi `CanCrossPage`) |
| `signature` | `text` or `column`, `align?` (`left` default, `center`, `right`), `width?` (inches, default 60% of the page), `height?` (room to sign, default 0.55), `after` | Signing line with its caption centered below; `column` takes the caption from the dataset (e.g. signer name and RUT) |
| `space` | `height` | Vertical space |

`after`: space below, in inches (default 0.06).

## Component specs

```json
{"name": "Devices", "source": {"class": "<Site>.Reports.LOGI.Common.spDevices", "query": "Data"},
 "tabs": ["Devices"],
 "layouts": [{"type": "table", "columns": [["Device", "Device", 0.4], ["Detail", "Detail", 0.6]]}]}
```

Layouts: `header`, `footer` (columns `Titulo`, `Institucion`, `Establecimiento`, `Unidad`, `LogoUrl`, `ImpresoEl`, `PieImpresion`, `PieCentro`, `PieDerecha`), `groupedList` (`groupBy`, `groupTitle`, `title`, `detail`, `titleWidth`), `table` (`columns: [caption, column, width fraction]`), `groupedTable` (`groupBy`, `groupTitle`, `columns` as in `table`: column titles once, then a heading row per group and its rows).

`groupBy` of `groupedList`/`groupedTable` is the sort key of the groups: return a numeric order column (`%Integer` in the ROWSPEC), not the title. A group heading sits in its own panel and can be left at a page end: check the PDF.

`table`/`groupedTable` accept `note: {label, column, indent?, labelWidth?}`: a second line inside each row ("label value", from `indent` × width to the right edge), e.g. the notes of an order item.

`section` (`title`, `band?` = `band`/`subband`/`title`, `columns`, `labelWidth`, `cells`, `after?`): a conditional block. The heading is the banded header and the banded object has `CompressIfNoRecord`, so a procedure that returns no rows hides the whole section. Use it from a main `component` block with a small `height` (e.g. 0.02, `after` 0): the subreport grows with its content.

A new layout is a new function in `isc_ai/logigen/report.py`, built from the XML of a product report.

## Deployer class

Generated per catalog. It carries the files as XData with their SHA-256 and the procedure list:

- `Deploy(pTargetDir="", .pWritten, pGrant=1, pConvert=..#CONVERT)`: writes into `websys.Configuration.GetJReportPath()/<folder>/`, verifies each hash, converts every file to binary `.cls`/`.cat` next to it (TrakCare converter started from the IRIS temp directory; one failed file is an error, the exit code is not trusted), grants EXECUTE and checks the privilege. Runs in any environment, also where no MCP runner is allowed.
- `Convert(pTargetDir="")`: only the binary conversion.
- `Verify(pTargetDir="", .pReport)`: read-only check of files, binaries (present, Logi header, not older than the XML) and grants.
