"""Minimal Logi Report 24.1 XML element builders (page report .cls.xml vocabulary).

Only the properties observed in TrakCare product reports (Logi 19/24 XML) are written;
Logi applies its defaults to every omitted property.
"""
from __future__ import annotations

import itertools
import xml.etree.ElementTree as ET

AUTOF = "${&#65;UTOF}"  # Logi marker for "automatic" values (serialized as ${&amp;#65;UTOF})

# Inches, the unit used by Logi XML. Font sizes are inches too (points / 72).
PT = 1 / 72


def num(value: float) -> str:
    return f"{value:.5f}"


def color(rgb: str) -> str:
    """'#2f6fd6' -> 'ff2f6fd6' (Logi ARGB)."""
    return "ff" + rgb.lstrip("#").lower()


def sub(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, tag, attrs)
    if text is not None:
        element.text = text
    return element


def props(element: ET.Element, values: dict[str, object]) -> ET.Element:
    for tag, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, float):
            value = num(value)
        sub(element, tag, str(value))
    return element


class Names:
    """Unique element names inside one report set (Logi requires unique Name per type family)."""

    def __init__(self) -> None:
        self._counters: dict[str, itertools.count] = {}

    def next(self, base: str) -> str:
        counter = self._counters.setdefault(base, itertools.count())
        index = next(counter)
        return base if index == 0 else f"{base}{index}"


STYLE_DEFAULTS = {
    "font": "*SourceSansPro-Regular",
    "size": 8.5,
    "color": "#333333",
    "background": None,
    "align": "Left",
    "valign": "Center",
    "wrap": False,
}


def _style(style: dict) -> dict:
    merged = dict(STYLE_DEFAULTS)
    merged.update(style or {})
    return merged


def _common(style: dict, x: float, y: float, w: float, h: float) -> dict:
    s = _style(style)
    return {
        "X": x,
        "Y": y,
        "Width": w,
        "Height": h,
        "FontFace": s["font"],
        "FontSize": s["size"] * PT,
        "Foreground": color(s["color"]),
        "Background": color(s["background"]) if s["background"] else None,
        "HorizontalAlignment": s["align"],
        "VerticalAlignment": s["valign"],
        "padding-left": 0.06,
        "padding-right": 0.06,
        "padding-top": 0.02,
        "padding-bottom": 0.02,
        "BorderWidth": 0.01389,
        "SameEncode": True,
        "refMode": "Mode",
        "GotoDetail": False,
        "LinkURLTargetFrame": "default",
    }


def label(parent: ET.Element, names: Names, text: str, x: float, y: float, w: float, h: float, style: dict | None = None) -> ET.Element:
    s = _style(style)
    element = sub(parent, "TextField", Name=names.next("Label"))
    values = {"DisplayName": text[:40] or "Label", "Text": text}
    values.update(_common(s, x, y, w, h))
    if s["wrap"]:
        values.update(_grow_down(w))
    return props(element, values)


def _grow_down(w: float) -> dict:
    """Wrap text and grow the height only: Auto Fit adjusts width AND height, so Maximum Width
    pins the width (Logi property reference, DBField: "Auto Fit" + "Maximum Width")."""
    return {"WordWrap": True, "AutoFit": True, "MaxWidth": w}


def db_field(parent: ET.Element, names: Names, mapping: str, x: float, y: float, w: float, h: float, style: dict | None = None) -> ET.Element:
    """Data field bound to a dataset field mapping name (Logi stores it upper case)."""
    s = _style(style)
    element = sub(parent, "DBField", Name=names.next(mapping))
    values = {
        "DisplayName": mapping,
        "RuntimeColumnName": mapping.upper(),
        "ColumnName": mapping.upper(),
        "Format": "XXXXXXXXX...",
    }
    values.update(_common(s, x, y, w, h))
    if s["wrap"]:
        values.update(_grow_down(w))
    return props(element, values)


def rule(parent: ET.Element, names: Names, x: float, y: float, w: float, h: float, rgb: str) -> ET.Element:
    """Horizontal rule drawn as an empty filled text field (portable across renderers)."""
    element = sub(parent, "TextField", Name=names.next("Rule"))
    return props(element, {
        "DisplayName": "Rule",
        "Text": "",
        "X": x,
        "Y": y,
        "Width": w,
        "Height": h,
        "Background": color(rgb),
        "BorderWidth": 0.0,
        "padding-left": 0.0,
        "padding-right": 0.0,
        "padding-top": 0.0,
        "padding-bottom": 0.0,
        "refMode": "Mode",
    })


def image(parent: ET.Element, names: Names, mapping: str, x: float, y: float, w: float, h: float) -> ET.Element:
    """Image whose URL comes from a dataset field (same binding syntax as product HeaderFooter)."""
    element = sub(parent, "Image", Name=names.next("Image"))
    return props(element, {
        "display": "inline",
        "DisplayName": "Logo",
        "X": x,
        "Y": y,
        "Width": w,
        "Height": h,
        "SameEncode": True,
        "ExportToCSV": False,
        "VerticalAlignment": "Center",
        "HorizontalAlignment": "Left",
        "ScalingMode": "Customize",
        "PictureName": f"@{mapping}|||0|@",
        "GotoDetail": False,
    })


def panel(parent: ET.Element, tag: str, name: str, w: float, h: float, invisible: bool = False,
          label_text: str | None = None, bottom_rule: str | None = None) -> ET.Element:
    """Report panel. bottom_rule draws only the bottom border (it moves with grown content,
    unlike a line element at a fixed Y)."""
    element = sub(parent, tag, Name=name)
    values = {
        "DisplayName": label_text or name,
        "Label": label_text or name,
        "Width": w,
        "Height": h,
        "Background": "ffffffff",
        "BorderWidth": 0.0,
        "Invisible": True if invisible else None,
    }
    if bottom_rule:
        values.update({
            "BorderWidth": 0.01042,
            "BorderColor": color(bottom_rule),
            "TopLine": "None",
            "LeftLine": "None",
            "RightLine": "None",
            "BottomLine": "Solid",
            "border-bottom-width": 0.01042,
            "border-bottom-color": color(bottom_rule),
        })
    return props(element, values)


def subreport(parent: ET.Element, names: Names, report_set: str, tab_name: str, tab_display: str,
              links: list[tuple[str, str]], sub_fields: list[str], x: float, y: float, w: float, h: float) -> ET.Element:
    """Subreport (same structure as the product HeaderFooter usage).

    links: (main column or parameter, subreport parameter) pairs, both Logi mapping names.
    """
    element = sub(parent, "SubReport", Name=names.next("Subreport"))
    field_list = ",".join(sub_fields)
    avl = f"'{len(links)}'" + "'".join(f"[{s.upper().replace('_', '__')}][{field_list}]" for _, s in links)
    props(element, {
        "AvlGrpFieldsMap": avl,
        "DisplayName": tab_display,
        "reportSet": report_set,
        "Report": tab_name,
        "SubDisplayName": tab_display,
        "Postfix": "AddToAdditionalPage",
        "Cache": True,
        "X": x,
        "Y": y,
        "Width": w,
        "Height": h,
        "BorderWidth": 0.0,
        "TopLine": "None",
        "LeftLine": "None",
        "RightLine": "None",
        "BottomLine": "None",
        "border-top-width": 0,
        "border-bottom-width": 0,
        "border-left-width": 0,
        "border-right-width": 0,
    })
    for index, (main, subcol) in enumerate(links):
        link = sub(element, "ParamSubLink", Name="ParamSubLink" if index == 0 else f"ParamSubLink{index}")
        props(link, {"MainColumn": main.upper(), "SubColumn": subcol.upper()})
    return element
