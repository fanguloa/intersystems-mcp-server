"""Read the SQL contract of Logi data sources from local ObjectScript class sources.

The ObjectScript class is the single source of truth: catalog procedures, report fields and
subreport links are derived from the Query ROWSPEC and formal parameters, never typed by hand.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re

QUERY_RE = re.compile(
    r"^Query\s+(?P<name>\w+)\((?P<args>[^)]*)\)\s+As\s+%Query\(ROWSPEC\s*=\s*\"(?P<rowspec>[^\"]*)\"\)\s*\[(?P<keywords>[^\]]*)\]",
    re.MULTILINE,
)
CLASS_RE = re.compile(r"^Class\s+(?P<name>[\w.]+)\s", re.MULTILINE)

INTEGER_TYPES = {"%Integer", "%Library.Integer", "%Boolean", "%Library.Boolean"}


@dataclasses.dataclass(frozen=True)
class Column:
    name: str
    sql_type: int  # java.sql.Types: 12 VARCHAR, 4 INTEGER
    type_name: str


@dataclasses.dataclass(frozen=True)
class StoredProcedure:
    class_name: str
    query: str
    params: tuple[Column, ...]
    columns: tuple[Column, ...]

    @property
    def sql_name(self) -> str:
        """SQL procedure name projected by IRIS: <ClassShortName>_<QueryName>."""
        return f"{self.class_name.rsplit('.', 1)[1]}_{self.query}"

    @property
    def schema(self) -> str:
        """SQL schema projected by IRIS: the package with dots replaced by underscores."""
        return self.class_name.rsplit(".", 1)[0].replace(".", "_")

    def column(self, name: str) -> Column:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(f"{self.sql_name} has no column {name}")


def _column(spec: str) -> Column:
    name, _, type_name = spec.strip().partition(":")
    type_name = type_name.strip() or "%String"
    if type_name in INTEGER_TYPES:
        return Column(name.strip(), 4, "INTEGER")
    return Column(name.strip(), 12, "VARCHAR")


def _params(args: str) -> tuple[Column, ...]:
    params = []
    for arg in filter(None, (a.strip() for a in args.split(","))):
        name = arg.split()[0]
        type_match = re.search(r"\bAs\s+([%\w.]+)", arg)
        params.append(_column(f"{name}:{type_match.group(1) if type_match else '%String'}"))
    return tuple(params)


def read_procedure(source_root: pathlib.Path, class_name: str, query: str) -> StoredProcedure:
    path = source_root.joinpath(*class_name.split(".")).with_suffix(".cls")
    text = path.read_text(encoding="utf-8")
    declared = CLASS_RE.search(text)
    if not declared or declared.group("name") != class_name:
        raise ValueError(f"{path} does not declare class {class_name}")
    for match in QUERY_RE.finditer(text):
        if match.group("name") != query:
            continue
        if "SqlProc" not in match.group("keywords"):
            raise ValueError(f"{class_name}:{query} is not projected as SqlProc")
        columns = tuple(_column(c) for c in match.group("rowspec").split(","))
        return StoredProcedure(class_name, query, _params(match.group("args")), columns)
    raise ValueError(f"{class_name} has no Query {query}")
