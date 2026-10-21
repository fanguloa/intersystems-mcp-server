"""Detect TrakCare ``websys.Feature`` references that a CCR promotion does not carry.

On TrakCare sites ``websys.Feature`` is in the change-control exclusion list, so an
ItemSet can load code that checks a Feature while the Feature itself never reaches
the target. The code then loads cleanly and stays switched off without any error.

This module is pure: source scanning, ItemSet path mapping and classification. Every
IRIS access goes through the caller's READ transport (``client.run_query`` and
``client.get_document``). Feature parameter VALUES are never returned, only names and
whether they match a reference, because sites keep endpoints and credentials there.
"""

import re

MAX_CLASSES = 60
MAX_CODES = 100

_CODE = r'"([A-Za-z][A-Za-z0-9_.]{0,63})"'
_CALL_RE = re.compile(
    r"\b(?:IsFeatureEnabled|GetFeatureParamAsBoolean|GetFeatureParams|GetFeatureParam|RegisterFeature)"
    r"\s*\(\s*" + _CODE
)
_PARAMETER_RE = re.compile(
    r"^\s*Parameter\s+\w*FEATURE\w*\s*(?:As\s+[%\w.]+\s*)?=\s*" + _CODE,
    re.IGNORECASE | re.MULTILINE,
)
_ITEM_CLASS_RE = re.compile(r"/cls/((?:[A-Za-z0-9]+/)*[A-Za-z0-9]+)\.xml(?:#.*)?$")
_CLASS_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)+")
_FEATURE_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,63}")

ITEMSET_ITEMS_SQL = (
    "SELECT element_key AS ItemPath FROM %Studio_SourceControl.ItemSet_ItemList WHERE ItemSet = ?"
)


def feature_rows_sql(count):
    placeholders = ", ".join("?" for _ in range(count))
    return (
        "SELECT ID, Code, "
        "CASE WHEN (DateFrom IS NULL OR DateFrom <= CURRENT_DATE) "
        "AND (DateTo IS NULL OR DateTo >= CURRENT_DATE) THEN 1 ELSE 0 END AS DatesCoverToday "
        f"FROM websys.Feature WHERE Code IN ({placeholders})"
    )


def feature_parameters_sql(count):
    placeholders = ", ".join("?" for _ in range(count))
    return (
        "SELECT ParRef->Code AS Code, Name, Value FROM websys.FeatureParameter "
        f"WHERE ParRef->Code IN ({placeholders})"
    )


def valid_feature_code(code):
    return isinstance(code, str) and _FEATURE_CODE_RE.fullmatch(code) is not None


def valid_class_name(name):
    return (isinstance(name, str) and not name.startswith("%") and not name.lower().endswith(".cls")
            and _CLASS_NAME_RE.fullmatch(name) is not None)


def extract_feature_codes(source_lines):
    """Feature codes referenced by literal calls or *FEATURE* class parameters."""
    text = "\n".join(source_lines) if isinstance(source_lines, (list, tuple)) else str(source_lines or "")
    codes = set(_CALL_RE.findall(text)) | set(_PARAMETER_RE.findall(text))
    return sorted(codes)


def itemset_paths_to_classes(paths):
    """Map ItemSet item paths (``.../cls/Pkg/Name.xml#head``) to class names.

    Non-class items (CT data, routines, CSP files) are ignored: only classes carry
    Feature checks the scanner can read.
    """
    classes = []
    for path in paths:
        match = _ITEM_CLASS_RE.search(str(path or ""))
        if not match:
            continue
        name = match.group(1).replace("/", ".")
        if valid_class_name(name) and name not in classes:
            classes.append(name)
    return classes


def _column(row, name):
    for key, value in row.items():
        if str(key).lower() == name.lower():
            return value
    return None


def _truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _read_features(client, codes):
    rows = client.run_query(feature_rows_sql(len(codes)), list(codes)) if codes else []
    found = {}
    for row in rows or []:
        code = _column(row, "Code")
        if code in codes:
            found[code] = {"id": _column(row, "ID"), "dates_cover_today": _truthy(_column(row, "DatesCoverToday"))}
    return found


def _read_parameters(client, codes):
    rows = client.run_query(feature_parameters_sql(len(codes)), list(codes)) if codes else []
    params = {}
    for row in rows or []:
        code = _column(row, "Code")
        name = _column(row, "Name")
        if code in codes and isinstance(name, str):
            params.setdefault(code, {})[name] = _column(row, "Value")
    return params


def _compare_parameters(target, reference):
    result = []
    for name in sorted(set(target) | set(reference)):
        if name not in target:
            status = "MISSING_IN_TARGET"
        elif name not in reference:
            status = "EXTRA_IN_TARGET"
        else:
            status = "MATCH" if (target[name] or "") == (reference[name] or "") else "DIFFERENT"
        result.append({"name": name, "status": status})
    return result


def scan_classes(client, class_names):
    """Read each class source (READ gate) and collect its Feature codes.

    Returns (references, unreadable, truncated) where references maps a code to the
    sorted classes that reference it.
    """
    truncated = len(class_names) > MAX_CLASSES
    references, unreadable = {}, []
    for name in class_names[:MAX_CLASSES]:
        try:
            lines = client.get_document(f"{name}.cls", strict=True)
        except Exception:
            unreadable.append(name)
            continue
        for code in extract_feature_codes(lines):
            references.setdefault(code, set()).add(name)
    return {code: sorted(owners) for code, owners in references.items()}, unreadable, truncated


def check_features(client, references, reference_client=None):
    """Classify every referenced code against the target (and optionally a reference).

    ``references`` maps code -> list of referencing classes (may be empty for codes
    requested explicitly). Raises on transport failures; callers decide fail-soft.
    """
    codes = sorted(references)[:MAX_CODES]
    found = _read_features(client, codes)
    features = []
    for code in codes:
        entry = {"code": code, "referenced_by": references[code]}
        if code in found:
            entry["status"] = "PRESENT"
            entry["dates_cover_today"] = found[code]["dates_cover_today"]
        else:
            entry["status"] = "MISSING"
        features.append(entry)

    if reference_client is not None and codes:
        ref_found = _read_features(reference_client, codes)
        ref_params = _read_parameters(reference_client, codes)
        target_params = _read_parameters(client, codes)
        for entry in features:
            code = entry["code"]
            entry["reference_status"] = "PRESENT" if code in ref_found else "MISSING"
            if code in ref_found or code in found:
                entry["parameters"] = _compare_parameters(target_params.get(code, {}), ref_params.get(code, {}))

    missing = [entry["code"] for entry in features if entry["status"] == "MISSING"]
    if not features:
        status = "FEATURE_CHECK_NO_REFERENCES"
    elif missing:
        status = "FEATURE_MISSING"
    else:
        status = "FEATURE_CHECK_OK"
    return {
        "status": status,
        "features": features,
        "missing": missing,
        "codes_truncated": len(references) > MAX_CODES,
    }


def render_markdown(record):
    """Short Markdown block for tool output; never includes parameter values."""
    status = record.get("status", "FEATURE_CHECK_UNAVAILABLE")
    if status == "FEATURE_CHECK_UNAVAILABLE":
        return f"⚠️ **FEATURE_CHECK_UNAVAILABLE**: {record.get('reason', 'feature check did not run')}"
    if status == "FEATURE_CHECK_NO_REFERENCES":
        text = "ℹ️ **FEATURE_CHECK_NO_REFERENCES**: no `websys.Feature` reference found in the scanned classes."
    elif status == "FEATURE_MISSING":
        text = (
            "⚠️ **FEATURE_MISSING**: code references `websys.Feature` codes that do not exist in this "
            "namespace. websys.Feature does not travel in CCR ItemSets; the code stays switched off "
            "until the Feature is registered here (separate, approved step)."
        )
    else:
        text = "✅ **FEATURE_CHECK_OK**: every referenced `websys.Feature` exists in this namespace."
    rows = []
    for entry in record.get("features", []):
        owners = ", ".join(f"`{name}`" for name in entry.get("referenced_by", [])) or "—"
        line = f"- `{entry['code']}`: **{entry['status']}**"
        if entry.get("status") == "PRESENT" and entry.get("dates_cover_today") is False:
            line += " (DateFrom/DateTo do not cover today)"
        if "reference_status" in entry:
            line += f"; reference **{entry['reference_status']}**"
        params = entry.get("parameters")
        if params:
            line += "; parameters " + ", ".join(f"`{p['name']}` {p['status']}" for p in params)
        line += f" — {owners}"
        rows.append(line)
    if rows:
        text += "\n\n" + "\n".join(rows)
    notes = []
    if record.get("classes_truncated"):
        notes.append(f"only the first {MAX_CLASSES} classes were scanned")
    if record.get("codes_truncated"):
        notes.append(f"only the first {MAX_CODES} codes were checked")
    if record.get("unreadable"):
        notes.append("unreadable: " + ", ".join(f"`{name}`" for name in record["unreadable"]))
    if notes:
        text += "\n\n_" + "; ".join(notes) + "._"
    return text
