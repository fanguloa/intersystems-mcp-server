"""Conservative, read-only CCR evidence. No configuration, I/O or authorization cache."""

from datetime import datetime, timezone
import re


class LocalPolicyError(ValueError):
    """A bounded, locally generated diagnostic safe to return to a tool caller."""


def validate_namespace(namespace):
    # %SYS is valid; percent-encoded path components are not.
    if not isinstance(namespace, str) or not re.fullmatch(
        r"(?:%[A-Za-z][A-Za-z0-9_-]*|[A-Za-z][A-Za-z0-9_-]*)", namespace
    ) or len(namespace) > 255 or re.search(r"%[0-9A-Fa-f]{2}", namespace):
        raise LocalPolicyError("Invalid namespace: use a plain namespace identifier.")
    return namespace.upper()


IDENTITY_SQL = "SELECT $NAMESPACE AS namespace"
ENGINE_SQL = (
    "SELECT $NAMESPACE AS namespace, COUNT(*) AS engine_count "
    "FROM %Dictionary.CompiledClass WHERE Name = 'TC.ChangeControl.Interface'"
)
PROJECTION_SQL = (
    "SELECT $NAMESPACE AS namespace, ROUTINE_SCHEMA, ROUTINE_NAME, EXTERNAL_NAME "
    "FROM INFORMATION_SCHEMA.ROUTINES "
    "WHERE ROUTINE_NAME = 'Extension_ExtensionEnabled'"
)
# Some IRIS versions expose an empty EXTERNAL_NAME in INFORMATION_SCHEMA.ROUTINES
# (observed on IRIS for TrakCare, INT UAT mirror 2026-09-11). The class dictionary is
# then the authoritative catalog mapping for the SqlProc projection.
METHOD_SQL = (
    "SELECT $NAMESPACE AS namespace, parent, Name, SqlName, SqlProc, ClassMethod "
    "FROM %Dictionary.CompiledMethod "
    "WHERE parent = '%Atelier.v1.Utils.Extension' AND Name = 'ExtensionEnabled'"
)
CLASS_SQL = (
    "SELECT $NAMESPACE AS namespace, Name, SqlSchemaName "
    "FROM %Dictionary.CompiledClass "
    "WHERE Name = '%Atelier.v1.Utils.Extension'"
)
# Read-only evidence that an Interoperability production is executing in this
# namespace on the queried node. Only a mirror primary runs production jobs; a
# backup member has read-only databases and no Ens.* jobs.
ACTIVE_NODE_SQL = (
    "SELECT $NAMESPACE AS namespace, COUNT(*) AS ens_jobs "
    "FROM %SYS.ProcessQuery WHERE NameSpace = $NAMESPACE AND Routine LIKE 'Ens.%'"
)


def unknown_record(namespace, canonical_route):
    return {
        "namespace": namespace,
        "canonical_route": canonical_route,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "identity_verified": False,
        "ccr_engine_present": None,
        "sourcecontrol_configured": None,
        "sourcecontrol_projection": None,
        "sc_class": None,
        "tc_binding": None,
        "subject_to_ccr": None,
        "applicability": "UNKNOWN",
        "discovery_status": "PARTIAL",
        "suspended": None,
        "suspend_level": None,
        "suspension_scope": "execution-process; not observed by namespace discovery",
        "reason": "Read-only namespace evidence unavailable; mutation blocked.",
    }


def _row(rows, namespace, fields):
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("Invalid metadata rows")
    row = rows[0]
    if set(row) != set(fields) | {"namespace"}:
        raise ValueError("Missing or unexpected metadata fields")
    if validate_namespace(row["namespace"]) != namespace:
        raise ValueError("Namespace identity mismatch")
    return row


def _bit(value):
    # Do not turn empty rows, nulls, floats or malformed counts into zero.
    # JSON booleans are a legitimate %Boolean projection over /action/query.
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    if type(value) is str and value in ("0", "1"):
        return value == "1"
    raise ValueError("Invalid metadata count")


def discover_namespace(namespace, canonical_route, query):
    """query is a strict /action/query metadata transport, never an executor.

    ExtensionEnabled reports generic source control, NOT the TC binding. Catalog
    identity is deliberately strict: unsupported EXTERNAL_NAME representations
    fail closed. No SQL schema alias or private-global probe is guessed.
    """
    namespace = validate_namespace(namespace)
    record = unknown_record(namespace, canonical_route)
    stage = "namespace identity"
    try:
        _row(query(IDENTITY_SQL), namespace, ())
        record["identity_verified"] = True
        stage = "TC engine metadata"
        row = _row(query(ENGINE_SQL), namespace, ("engine_count",))
        record["ccr_engine_present"] = _bit(row["engine_count"])
        stage = "verified source-control SQL projection (catalog mapping required)"
        row = _row(query(PROJECTION_SQL), namespace,
                   ("ROUTINE_SCHEMA", "ROUTINE_NAME", "EXTERNAL_NAME"))
        schema, name = row["ROUTINE_SCHEMA"], row["ROUTINE_NAME"]
        if (not isinstance(schema, str) or len(schema) > 128
                or not re.fullmatch(r"[%A-Za-z][A-Za-z0-9_]*", schema)):
            raise ValueError("Invalid SQL schema")
        if name != "Extension_ExtensionEnabled" or row["EXTERNAL_NAME"] not in (
            "%Atelier.v1.Utils.Extension.ExtensionEnabled",
            "%Atelier.v1.Utils.Extension:ExtensionEnabled",
            "",
            None,
        ):
            raise ValueError("Unverified source-control projection")
        if row["EXTERNAL_NAME"] in ("", None):
            method = _row(query(METHOD_SQL), namespace,
                          ("parent", "Name", "SqlName", "SqlProc", "ClassMethod"))
            if (method["parent"] != "%Atelier.v1.Utils.Extension"
                    or method["Name"] != "ExtensionEnabled"
                    or method["SqlName"] != name
                    or _bit(method["SqlProc"]) is not True
                    or _bit(method["ClassMethod"]) is not True):
                raise ValueError("Unverified source-control projection")
            owner = _row(query(CLASS_SQL), namespace, ("Name", "SqlSchemaName"))
            if (owner["Name"] != "%Atelier.v1.Utils.Extension"
                    or owner["SqlSchemaName"] != schema):
                raise ValueError("Unverified source-control projection")
        projection = f'"{schema}"."{name}"'
        stage = "source-control configured flag"
        row = _row(query(f"SELECT $NAMESPACE AS namespace, {projection}() AS configured"),
                   namespace, ("configured",))
        record["sourcecontrol_configured"] = _bit(row["configured"])
        record["sourcecontrol_projection"] = f"{schema}.{name}"
    except Exception:
        # Metadata only: never copy SQL, response bodies, config or exceptions.
        record["reason"] = f"Unable to verify {stage}; mutation blocked. Check read-only metadata access and target compatibility."
        return record

    if record["ccr_engine_present"] is False and record["sourcecontrol_configured"] is False:
        record.update(subject_to_ccr=False, applicability="ABSENT", discovery_status="SUFFICIENT_ABSENCE",
                      reason="TC engine absent and generic source control unconfigured on the verified namespace route. Other write gates still apply.")
    else:
        record["reason"] = (
            "Missing pure read-only TC binding probe: generic source control and engine installation "
            "do not establish CCR applicability. Mutation/activation blocked pending an authorized "
            "backend facility with execution-process suspension revalidation."
        )
    return record


def sufficient_absence(record, namespace, canonical_route):
    """Only fresh transport evidence can reach the limited non-CCR route."""
    return (
        isinstance(record, dict)
        and record.get("namespace") == namespace
        and record.get("canonical_route") == canonical_route
        and record.get("identity_verified") is True
        and record.get("applicability") == "ABSENT"
        and record.get("discovery_status") == "SUFFICIENT_ABSENCE"
        and record.get("subject_to_ccr") is False
        and record.get("ccr_engine_present") is False
        and record.get("sourcecontrol_configured") is False
        and isinstance(record.get("sourcecontrol_projection"), str)
        and bool(record["sourcecontrol_projection"])
        and record.get("suspended") is not True
    )


def active_node(namespace, query):
    """Read-only evidence that this node executes the namespace production.

    In an IRIS mirror only the primary runs Interoperability jobs; a backup has
    read-only databases and no Ens.* processes. Any transport or shape error is
    UNKNOWN (False), never an assumption of activity.
    """
    try:
        namespace = validate_namespace(namespace)
        row = _row(query(ACTIVE_NODE_SQL), namespace, ("ens_jobs",))
        jobs = row["ens_jobs"]
        if type(jobs) is str and jobs.isdigit():
            jobs = int(jobs)
        return type(jobs) is int and jobs > 0
    except Exception:
        return False
