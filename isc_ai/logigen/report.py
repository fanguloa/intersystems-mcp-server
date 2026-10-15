"""Compose Logi page reports (.cls.xml) from declarative JSON specs.

A spec declares the dataset (an ObjectScript SqlProc), the page, the components used as
subreports and the blocks of the body. Layout coordinates are computed here, so a report is
written as "what goes where", never as Designer coordinates.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from . import xmlkit as k
from .catalog import CatalogModel

PAGES = {"A4": (8.26772, 11.69291), "Letter": (8.5, 11.0)}

DEFAULT_THEME = {
    "primary": "#2f80ed",
    "primaryText": "#ffffff",
    "accent": "#1f5fbf",
    "soft": "#eaf2fd",
    "muted": "#f4f5f7",
    "rule": "#d9dee5",
    "label": "#1d2b36",
    "text": "#333333",
    "faint": "#6b7785",
}

# Active palette. The builders read it; use_theme() sets it per catalog from the manifest.
THEME = dict(DEFAULT_THEME)


def use_theme(overrides: dict | None) -> None:
    """Default palette plus the manifest "theme" overrides (unknown keys are a contract error)."""
    unknown = set(overrides or {}) - set(DEFAULT_THEME)
    if unknown:
        raise KeyError(f"unknown theme keys {sorted(unknown)}")
    THEME.clear()
    THEME.update(DEFAULT_THEME)
    THEME.update(overrides or {})

ROW = 0.24
GAP = 0.06


HEADINGS = ("band", "subband", "title")


def _band(panel, names, text, w, kind, y=0.0):
    """Section heading at y; returns the height used (heading + its spacing)."""
    if kind == "band":
        style = {"font": "*SourceSansPro-SemiBold", "size": 8.5, "color": THEME["primaryText"], "background": THEME["primary"]}
        k.label(panel, names, text, 0.0, y, w, 0.24, style)
        return 0.24 + GAP
    if kind == "subband":
        style = {"font": "*SourceSansPro-SemiBold", "size": 8, "color": THEME["accent"], "background": THEME["soft"]}
        k.label(panel, names, text, 0.0, y, w, 0.22, style)
        return 0.22 + GAP
    style = {"font": "*SourceSansPro-SemiBold", "size": 10, "color": THEME["accent"]}
    k.label(panel, names, text, 0.0, y, w, 0.26, style)
    k.rule(panel, names, 0.0, y + 0.27, w, 0.01, THEME["rule"])
    return 0.29 + GAP


def _headings(panel, names, headings, w):
    """Draws the pending headings at the top of a panel; returns the Y where content starts."""
    y = 0.0
    for heading in headings:
        y += _band(panel, names, heading["text"], w, heading["type"], y)
    return y


def _grid_row(panel, names, block, cells, w, field, y=0.0):
    """One grid row: label/value pairs. Each row is its own panel so wrapped values grow safely."""
    columns = block.get("columns", 2)
    label_w = block.get("labelWidth", 1.15)
    cell_w = w / columns
    label_style = {"font": "*SourceSansPro-SemiBold", "size": 8, "color": THEME["label"], "wrap": True}
    value_style = {"size": 8, "color": THEME["text"], "wrap": True}
    for col, (caption, column) in enumerate(cells):
        x = col * cell_w
        k.label(panel, names, caption, x, y, label_w, ROW, label_style)
        if column:
            k.db_field(panel, names, field(column), x + label_w, y, cell_w - label_w, ROW, value_style)
    # Row separator = panel bottom border, so it moves down when a wrapped value grows
    return y + ROW + 0.02


def _textbox(panel, names, block, w, field, y=0.0):
    caption = block.get("label")
    if caption:
        k.label(panel, names, caption, 0.0, y, w, ROW, {"font": "*SourceSansPro-SemiBold", "size": 8, "color": THEME["label"]})
        y += ROW
    height = block.get("height", 0.45)
    k.db_field(panel, names, field(block["column"]), 0.08, y, w - 0.16, height,
               {"size": 8, "color": THEME["text"], "background": THEME["muted"], "wrap": True, "valign": "Top"})
    return y + height


SIGNATURE_ALIGN = ("left", "center", "right")


def _signature(panel, names, block, w, field, y=0.0):
    """Signature space: blank room to sign, the signing line and its caption centered below.
    The caption is a fixed "text" or a dataset "column" (e.g. the signer's name and RUT);
    "align" places the line at the left (default), center or right of the page. Optional
    "imageColumn" is a column with the file path or URL of the signature image, drawn in the
    signing room just above the line (same binding as the header logo)."""
    line_w = min(block.get("width", w * 0.6), w)
    align = block.get("align", "left")
    if align not in SIGNATURE_ALIGN:
        raise ValueError(f"unknown signature align {align}")
    x = {"left": 0.0, "center": (w - line_w) / 2, "right": w - line_w}[align]
    room = block.get("height", 0.55)
    if block.get("imageColumn"):
        image_h = max(room - 0.04, 0.1)
        image_w = min(block.get("imageWidth", image_h * 2.5), line_w)
        k.image(panel, names, field(block["imageColumn"]), x + (line_w - image_w) / 2, y + 0.02, image_w, image_h,
                align="Center", display_name="Firma", constrain=True)
    y += room
    k.rule(panel, names, x, y, line_w, 0.012, THEME["faint"])
    style = {"size": 7.5, "color": THEME["faint"], "align": "Center", "wrap": True}
    caption_h = block.get("captionHeight", 0.2)
    if block.get("column"):
        k.db_field(panel, names, field(block["column"]), x, y + 0.04, line_w, caption_h, style)
    else:
        k.label(panel, names, block.get("text", ""), x, y + 0.04, line_w, caption_h, style)
    return y + caption_h + 0.04


def _component(panel, names, block, w, ctx, y=0.0):
    component = ctx["components"][block["component"]]
    tab = component["tabs"][block.get("tab", 0)]
    main_columns = ctx.get("main_columns", set())
    links = []
    for main, sub in block["links"].items():
        # A link source is a main dataset column (resolved to its catalog mapping name) or a parameter
        links.append((ctx["field"](main) if main in main_columns else main, sub))
    sub_fields = [ctx["model"].field(component["dataset"], c.name) for c in ctx["model"].procedure(component["dataset"]).columns]
    height = block.get("height", 0.6)
    # Reference the XML report set: Logi renders .cls.xml directly, so no binary conversion is
    # needed in any environment (the TrakCare converter was observed failing in a BASE)
    k.subreport(panel, names, component["name"] + ".cls.xml", tab["id"], tab["display"], links, sub_fields, 0.0, y, w, height)
    return y + height


def _body_panels(banded_page_panel, names, blocks, w, ctx, field):
    """Stacked detail panels. Headings (band/subband/title) are drawn inside the panel of the
    first row of the next block: a detail panel is not split across pages (Logi Cross Page
    defaults to false), so a heading is never left alone at the bottom of a page."""
    pending = []
    for block in blocks:
        kind = block["type"]
        if kind in HEADINGS:
            pending.append(block)
            continue
        if kind == "grid":
            columns = block.get("columns", 2)
            cells = block["cells"]
            for start in range(0, len(cells), columns):
                panel = k.panel(banded_page_panel, "DetailPanel", names.next("DetailPanel"), w, 0.1,
                                label_text="DetailPanel", bottom_rule=THEME["rule"])
                y = _headings(panel, names, pending, w)
                pending = []
                used = _grid_row(panel, names, block, cells[start:start + columns], w, field, y)
                panel.find("Height").text = k.num(used)
            if block.get("after"):
                k.panel(banded_page_panel, "DetailPanel", names.next("DetailPanel"), w, block["after"], label_text="DetailPanel")
            continue
        panel = k.panel(banded_page_panel, "DetailPanel", names.next("DetailPanel"), w, 0.1, label_text="DetailPanel")
        if block.get("crossPage"):
            # Logi "Cross Page" (XML CanCrossPage): a long subreport splits at the page break
            # instead of moving whole to the next page and leaving the previous one blank
            k.props(panel, {"CanCrossPage": True})
        y = _headings(panel, names, pending, w)
        pending = []
        if kind == "textbox":
            used = _textbox(panel, names, block, w, field, y)
        elif kind == "component":
            used = _component(panel, names, block, w, ctx, y)
        elif kind == "signature":
            used = _signature(panel, names, block, w, field, y)
        elif kind == "space":
            used = y + block.get("height", 0.1)
        else:
            raise ValueError(f"unknown block type {kind}")
        height = used + block.get("after", GAP)
        panel.find("Height").text = k.num(height)
    if pending:
        panel = k.panel(banded_page_panel, "DetailPanel", names.next("DetailPanel"), w, 0.1, label_text="DetailPanel")
        panel.find("Height").text = k.num(_headings(panel, names, pending, w))


def _report_shell(reports, names, tab, page, dataset_name):
    width, height = PAGES[page.get("size", "A4")]
    if page.get("orientation", "Portrait") == "Landscape":
        width, height = height, width
    margin = page.get("margin", 0.39)
    content_w = width - 2 * margin

    report = k.sub(reports, "Report", Name=tab["id"])
    k.props(report, {
        "type": 0,
        "IgnoreTOCAnchorofParent": True,
        "NoPageBreak": True,
        "NoPageBreakForRptFormat": True,
        "nationalLanguageSupport": True,
        "NoPageBreakForDataFormat": True,
        "FullFillAndBorder": True,
        "ClickPriority": "link~@CR7~drill_down~@CR7~go_to_detail",
        "status": 0,
        "ExportFormat": "PDF",
        "DisplayName": tab["display"],
    })
    body = k.sub(report, "ReportBody", Name="ReportBody" if tab["index"] == 0 else f"ReportBody{tab['index']}")
    k.props(body, {
        "DisplayName": "Report Body",
        "Height": -0.01042,
        "Width": -0.01042,
        "Background": "ffffffff",
        "dataSet": dataset_name,
        "dataInherit": False,
        "Cache": True,
    })
    start = k.sub(body, "PageStart", Name=names.next("PageStart"))
    k.props(start, {"DisplayName": "Page Start", "pageReference": names.peek_page(tab)})
    banded = k.sub(body, "BandedObject", Name=names.next("BandedObject"))
    k.props(banded, {
        "DisplayName": "Banded Object",
        "dataSet": dataset_name,
        "dataInherit": False,
        "StyleFile": "DefaultSequential.css",
        "StyleList": "DefaultSequential.css",
        "StyleInherit": False,
        "ShowSubHeaderFooter": False,
        "CompressIfNoRecord": False,
        "Cache": True,
        "Width": content_w,
        "Height": 0.8,
        "BorderWidth": 0.0,
    })
    k.panel(banded, "BandedHeader", names.next("BandedHeader"), content_w, 0.01, invisible=True, label_text="BandedHeader")
    page_panel = k.sub(banded, "BandedPagePanel", Name=names.next("BandedPagePanel"))
    k.props(page_panel, {
        "DisplayName": "Banded Page Panel",
        "Width": width,
        "Height": height,
        "PageBackground": "ffffffff",
        "TopMargin": margin,
        "BottomMargin": margin,
        "LeftMargin": margin,
        "RightMargin": margin,
    })
    return report, body, banded, page_panel, content_w, (width, height, margin)


def _report_tail(report, body, banded, names, tab, content_w, page_geometry):
    width, height, margin = page_geometry
    k.panel(banded, "BandedFooter", names.next("BandedFooter"), content_w, 0.01, invisible=True, label_text="BandedFooter")
    end = k.sub(body, "PageEnd", Name=names.next("PageEnd"))
    k.props(end, {"DisplayName": "Page End", "pageReference": names.peek_page(tab)})
    views = k.sub(report, "Views", Name=names.next("Views"))
    view = k.sub(views, "View", Name=names.next("View"))
    k.props(view, {"StyleList": "DefaultSequential.css", "CSSFileName": "DefaultSequential.css"})
    modes = k.sub(report, "ModeContainer", Name=names.next("ModeContainer"))
    mode = k.sub(modes, "Mode", Name=names.next("Mode"))
    k.props(mode, {"currentRender": 0})
    render = k.sub(mode, "RenderText", Name=names.next("RenderText"))
    k.props(render, {"rTitle": k.AUTOF})
    k.sub(report, "ActionContainer", Name=names.next("ActionContainer"))
    k.sub(report, "BuildInFilterCtrlContainer", Name=names.next("BuildInFilterCtrlContainer"))
    toc = k.sub(report, "TOC")
    k.props(toc, {"DisplayName": "TOC", "FontFace": "Arial", "FontSize": 0.12})
    k.sub(report, "ConditionalFormatSet", Name=names.next("ConditionalFormatSet"))
    k.sub(report, "ReportFormulaClassSet", Name=names.next("ReportFormulaClassSet"))
    page = k.sub(report, "PagePanel", Name=names.peek_page(tab))
    k.props(page, {
        "Height": height,
        "Width": width,
        "PageType": "Custom size",
        "Orientation": "Portrait" if width < height else "Landscape",
        "TopMargin": margin,
        "BottomMargin": margin,
        "LeftMargin": margin,
        "RightMargin": margin,
        "DisplayName": "Page Panel",
    })
    for tag in ("PageHeaderPanel", "PageFooterPanel"):
        p = k.panel(page, tag, names.next(tag), content_w, 0.2, invisible=True, label_text=tag)
        k.props(p, {"Suppressed": True})
    k.sub(report, "ReportUFormulaSet", Name=names.next("ReportUFormulaSet"))


class ReportNames(k.Names):
    def peek_page(self, tab) -> str:
        return "PagePanel" if tab["index"] == 0 else f"PagePanel{tab['index']}"


def _report_set(name: str) -> ET.Element:
    root = ET.Element("ReportSet", {
        "Name": name,
        "Serial": "65672",
        "version": "1.2",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:noNamespaceSchemaLocation": "",
    })
    k.props(root, {
        "tplName": "reportset1",
        "curReportId": "Report",
        "ServicePack": 3,
        "RptTemplateNumber": 240,
        "BuildNumber": 19030,
        "major": 19,
        "dot3": 3,
        "DisplayName": "reportset1",
    })
    return root


def _datasets(root, model: CatalogModel, sql_name: str, extra: list[str], ds_name: str):
    datasets = k.sub(root, "DataSets", Name="DataSets")
    ds = k.sub(datasets, "QueryDataSet", Name="QueryDataSet")
    names = model.dataset_mapping_names(sql_name) + [e for e in extra if e not in model.dataset_mapping_names(sql_name)]
    k.props(ds, {
        # Catalog data source of the query; without it Logi looks in "Data Source 1"
        "dsName": ds_name,
        "MaxRecords": -1,
        "RecordsPerPage": -1,
        "SrcListType": 1,
        "queryName": sql_name,
        "MaxPageNumber": -1,
        "DisplayName": sql_name,
        "additionalMappingNames": f"'{len(names)}'" + "'".join(names),
    })


def _tabs(spec):
    return [{"id": "Report" if i == 0 else f"Report{i}", "display": t, "index": i} for i, t in enumerate(spec.get("tabs", ["report 1"]))]


def build_report(spec: dict, model: CatalogModel, components: dict) -> ET.ElementTree:
    """Main report: one-row dataset, header/footer subreports and stacked detail panels."""
    sql_name = spec["dataset"]
    names = ReportNames()
    field = lambda column: model.field(sql_name, column)  # noqa: E731 - KeyError = contract violation
    ctx = {"model": model, "components": components, "field": field,
           "main_columns": {c.name for c in model.procedure(sql_name).columns}}
    root = _report_set(spec["name"])
    reports = k.sub(root, "Reports", Name="Reports")
    tab = _tabs(spec)[0]
    report, body, banded, page_panel, content_w, geometry = _report_shell(reports, names, tab, spec.get("page", {}), "QueryDataSet")

    header = k.panel(page_panel, "BandedPageHeader", names.next("BandedPageHeader"), content_w, 0.1, label_text="BandedPageHeader")
    header.find("Height").text = k.num(_component(header, names, spec["pageHeader"], content_w, ctx) + 0.08)
    _body_panels(page_panel, names, spec["blocks"], content_w, ctx, field)
    if spec.get("recordPerPage"):
        # One document per dataset row (e.g. a batch of orders): the first body panel of every
        # record starts a new page (Logi "On New Page", product archivo/EtiquetaDeSeguimiento)
        k.props(page_panel.find("DetailPanel"), {"OnNewPage": True})
    # Product pattern (rce/IndicacionesDiarias): the page footer is suppressed on the last page and
    # the banded footer, pushed to the page bottom, prints the same footer there. Otherwise the
    # last page footer follows the content instead of sitting at the bottom of the sheet.
    footer = k.panel(page_panel, "BandedPageFooter", names.next("BandedPageFooter"), content_w, 0.1, label_text="BandedPageFooter")
    k.props(footer, {"SuppressInLastPage": True, "TopLine": "None", "LeftLine": "None", "RightLine": "None", "BottomLine": "None"})
    footer.find("Height").text = k.num(_component(footer, names, spec["pageFooter"], content_w, ctx))

    _report_tail(report, body, banded, names, tab, content_w, geometry)
    last_footer = banded.find("BandedFooter")
    last_footer.remove(last_footer.find("Invisible"))
    k.props(last_footer, {"ToBottom": True, "TopLine": "None", "LeftLine": "None", "RightLine": "None", "BottomLine": "None"})
    last_footer.find("Height").text = k.num(_component(last_footer, names, spec["pageFooter"], content_w, ctx))
    _datasets(root, model, sql_name, spec.get("extraParameters", []), model.data_source)
    return ET.ElementTree(root)


def build_component(spec: dict, model: CatalogModel) -> ET.ElementTree:
    """Component report set (subreport): one tab per layout, all bound to the component dataset."""
    sql_name = spec["dataset"]
    names = ReportNames()
    field = lambda column: model.field(sql_name, column)  # noqa: E731
    root = _report_set(spec["name"])
    reports = k.sub(root, "Reports", Name="Reports")
    for tab, layout in zip(_tabs(spec), spec["layouts"]):
        report, body, banded, page_panel, content_w, geometry = _report_shell(reports, names, tab, spec.get("page", {}), "QueryDataSet")
        width = layout.get("width", content_w)
        kind = layout["type"]
        if kind == "header":
            _header_layout(page_panel, names, width, field)
        elif kind == "footer":
            _footer_layout(page_panel, names, width, field)
        elif kind == "groupedList":
            _grouped_list_layout(page_panel, names, width, field, layout)
        elif kind == "table":
            _table_layout(banded, page_panel, names, width, field, layout)
        elif kind == "groupedTable":
            _grouped_table_layout(banded, page_panel, names, width, field, layout)
        elif kind == "section":
            _section_layout(banded, page_panel, names, width, field, layout)
        else:
            raise ValueError(f"unknown layout {kind}")
        _report_tail(report, body, banded, names, tab, content_w, geometry)
    _datasets(root, model, sql_name, [], model.data_source)
    return ET.ElementTree(root)


def _header_layout(page_panel, names, w, field):
    panel = k.panel(page_panel, "DetailPanel", names.next("DetailPanel"), w, 0.86, label_text="DetailPanel")
    # The logo keeps its proportions inside the box (a fixed box stretched non-matching images)
    k.image(panel, names, field("LogoUrl"), 0.0, 0.05, 1.45, 0.62, constrain=True)
    # Long titles wrap up to three lines inside the space between the logo and the print data
    k.db_field(panel, names, field("Titulo"), 1.55, 0.04, w - 3.8, 0.70,
               {"font": "*SourceSansPro-SemiBold", "size": 13, "color": THEME["primary"], "align": "Center",
                "valign": "Center", "wrap": True, "grow": False})
    right = {"size": 7, "color": THEME["faint"], "align": "Right"}
    k.db_field(panel, names, field("ImpresoEl"), w - 2.2, 0.06, 2.2, 0.17, right)
    k.db_field(panel, names, field("Establecimiento"), w - 2.2, 0.23, 2.2, 0.17, right)
    k.db_field(panel, names, field("Unidad"), w - 2.2, 0.40, 2.2, 0.17, right)
    k.rule(panel, names, 0.0, 0.80, w, 0.02, THEME["primary"])


def _footer_layout(page_panel, names, w, field):
    panel = k.panel(page_panel, "DetailPanel", names.next("DetailPanel"), w, 0.26, label_text="DetailPanel")
    k.rule(panel, names, 0.0, 0.0, w, 0.006, THEME["rule"])
    style = {"size": 6.5, "color": THEME["faint"]}
    third = w / 3
    k.db_field(panel, names, field("PieImpresion"), 0.0, 0.04, third, 0.18, style)
    k.db_field(panel, names, field("PieCentro"), third, 0.04, third, 0.18, dict(style, align="Center"))
    k.db_field(panel, names, field("PieDerecha"), 2 * third, 0.04, third, 0.18, dict(style, align="Right"))


def _grouped_list_layout(page_panel, names, w, field, layout):
    group = k.sub(page_panel, "GroupPanel", Name=names.next("GroupPanel"))
    k.props(group, {"GroupBy": field(layout["groupBy"]).upper(), "DisplayName": "Group Panel", "SelectN": -1})
    header = k.panel(group, "GroupHeaderPanel", names.next("GroupHeaderPanel"), w, 0.26, label_text="GroupHeaderPanel")
    k.db_field(header, names, field(layout["groupTitle"]), 0.0, 0.02, w, 0.22,
               {"font": "*SourceSansPro-SemiBold", "size": 8, "color": THEME["label"], "background": THEME["muted"]})
    detail = k.panel(group, "DetailPanel", names.next("DetailPanel"), w, ROW, label_text="DetailPanel")
    title_w = layout.get("titleWidth", 2.6)
    k.db_field(detail, names, field(layout["title"]), 0.08, 0.0, title_w, ROW, {"font": "*SourceSansPro-SemiBold", "size": 8, "color": THEME["text"], "wrap": True})
    k.db_field(detail, names, field(layout["detail"]), 0.08 + title_w, 0.0, w - title_w - 0.08, ROW, {"size": 8, "color": THEME["faint"], "wrap": True})
    k.panel(group, "GroupFooterPanel", names.next("GroupFooterPanel"), w, 0.06, label_text="GroupFooterPanel")


def _table_header(banded, names, w, columns):
    """Column titles in the banded header (printed once, also inside a subreport);
    a banded page header is not printed when the report runs as a subreport."""
    header = banded.find("BandedHeader")
    invisible = header.find("Invisible")
    if invisible is not None:
        header.remove(invisible)
    header.find("Height").text = k.num(0.26)
    x = 0.0
    for caption, _column, width in columns:
        k.label(header, names, caption, x, 0.02, width * w, 0.22,
                {"font": "*SourceSansPro-SemiBold", "size": 8, "color": THEME["label"], "background": THEME["muted"]})
        x += width * w


def _table_row(detail, names, w, field, columns):
    x = 0.0
    for _caption, column, width in columns:
        k.db_field(detail, names, field(column), x, 0.0, width * w, ROW, {"size": 8, "color": THEME["text"], "wrap": True})
        x += width * w
    return ROW


def _table_note(detail, names, w, field, note, y):
    """Optional second line of a table row: "label: value" from column "indent" (fraction of the
    width) to the right edge, in the same panel so it grows and breaks with its row."""
    x = note.get("indent", 0.0) * w
    label_w = note.get("labelWidth", 0.9)
    k.label(detail, names, note.get("label", ""), x, y, label_w, ROW,
            {"font": "*SourceSansPro-SemiBold", "size": 7.5, "color": THEME["label"]})
    k.db_field(detail, names, field(note["column"]), x + label_w, y, w - x - label_w, ROW,
               {"size": 7.5, "color": THEME["faint"], "wrap": True})
    return y + ROW


def _table_detail(page_panel, names, w, field, layout, parent=None):
    detail = k.panel(parent if parent is not None else page_panel, "DetailPanel", names.next("DetailPanel"), w, ROW,
                     label_text="DetailPanel", bottom_rule=THEME["rule"])
    used = _table_row(detail, names, w, field, layout["columns"])
    if layout.get("note"):
        used = _table_note(detail, names, w, field, layout["note"], used)
    detail.find("Height").text = k.num(used)
    return detail


def _table_layout(banded, page_panel, names, w, field, layout):
    _table_header(banded, names, w, layout["columns"])
    _table_detail(page_panel, names, w, field, layout)


def _section_layout(banded, page_panel, names, w, field, layout):
    """Conditional section: a band heading and a label/value grid that are printed only when the
    component dataset returns a row. The heading is the banded header and the banded object
    compresses when there is no record (Logi "CompressIfNoRecord"), so a procedure that returns
    no rows hides the whole section. Link it from a main report "component" block with a small
    height: the subreport grows with its content."""
    banded.find("CompressIfNoRecord").text = "true"
    header = banded.find("BandedHeader")
    invisible = header.find("Invisible")
    if invisible is not None:
        header.remove(invisible)
    header.find("Height").text = k.num(_band(header, names, layout["title"], w, layout.get("band", "band")))
    columns = layout.get("columns", 2)
    cells = layout["cells"]
    for start in range(0, len(cells), columns):
        panel = k.panel(page_panel, "DetailPanel", names.next("DetailPanel"), w, 0.1,
                        label_text="DetailPanel", bottom_rule=THEME["rule"])
        panel.find("Height").text = k.num(_grid_row(panel, names, layout, cells[start:start + columns], w, field))
    if layout.get("after"):
        k.panel(page_panel, "DetailPanel", names.next("DetailPanel"), w, layout["after"], label_text="DetailPanel")


def _grouped_table_layout(banded, page_panel, names, w, field, layout):
    """Table with section rows: column titles once, then per group a heading row (subband
    style) and its detail rows. Logi groups by "groupBy": return it as a sortable order."""
    columns = layout["columns"]
    _table_header(banded, names, w, columns)
    group = k.sub(page_panel, "GroupPanel", Name=names.next("GroupPanel"))
    k.props(group, {"GroupBy": field(layout["groupBy"]).upper(), "DisplayName": "Group Panel", "SelectN": -1})
    header = k.panel(group, "GroupHeaderPanel", names.next("GroupHeaderPanel"), w, 0.24,
                     label_text="GroupHeaderPanel", bottom_rule=THEME["rule"])
    k.db_field(header, names, field(layout["groupTitle"]), 0.0, 0.0, w, 0.24,
               {"font": "*SourceSansPro-SemiBold", "size": 7.5, "color": THEME["accent"], "background": THEME["soft"]})
    _table_detail(page_panel, names, w, field, layout, parent=group)
    k.panel(group, "GroupFooterPanel", names.next("GroupFooterPanel"), w, 0.01, label_text="GroupFooterPanel")
