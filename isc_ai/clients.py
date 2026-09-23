"""Install a missing AI client from the recipe declared in the baseline.

This is the only part of the CLI that changes the system outside configuration files, so
it is the most explicit one:

  * plan by default; `--apply` is required to execute anything
  * one confirmation per client, and the exact command is printed before it is asked
  * the confirmation is interactive only when stdin is a terminal. This same binary serves
    MCP over stdio: a prompt on a pipe would hang the client that launched it, so without
    a terminal an explicit `--yes` is required instead
  * a client that is already installed is never touched
  * the package manager must exist first, and the result is verified by asking the
    installed program for its version
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from isc_ai import audit
from isc_ai import config as isc_config

TIMEOUT = 900


def _version_of(binary, argv):
    resolved = shutil.which(binary)
    if not resolved:
        return None
    try:
        # The resolved path, never the bare name: on Windows these clients are `.cmd`
        # shims and CreateProcess does not apply PATHEXT.
        result = subprocess.run([resolved, *argv], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or result.stderr).strip().splitlines()[0] if result.stdout or result.stderr else ""


def plan(names=None, home=None, environ=None, suite_path=None):
    """State of every client the suite knows, and what installing it would run."""
    environ = os.environ if environ is None else environ
    suite = audit.load_suite(suite_path)
    wanted = set(names or ())
    report = {"mode": "check", "clients": []}
    for name, spec in suite["clients"].items():
        if name.startswith("$") or (wanted and name not in wanted):
            continue
        binary = spec.get("binary") or name
        recipe = spec.get("install") or {}
        manager = recipe.get("manager") or ""
        argv = list(recipe.get("argv") or [])
        version = _version_of(binary, spec.get("version_argv") or ["--version"])
        report["clients"].append({
            "client": name,
            "installed": shutil.which(binary) is not None,
            "version": version,
            "command": " ".join(argv) if argv else None,
            "manager": manager or None,
            "manager_available": bool(shutil.which(manager)) if manager else False,
        })
    unknown = wanted - {item["client"] for item in report["clients"]}
    if unknown:
        available = ", ".join(sorted(name for name in suite["clients"] if not name.startswith("$")))
        raise isc_config.ConfigError(f"unknown client(s): {', '.join(sorted(unknown))}. "
                                    f"Available: {available}.")
    return report


def _confirm(record, assume_yes, stream=None, output=None):
    """Ask once, and only where a human can answer."""
    stream = sys.stdin if stream is None else stream
    output = sys.stderr if output is None else output
    command = record["command"]
    if assume_yes:
        return True, "confirmed by --yes"
    if not stream.isatty():
        return False, ("no terminal to confirm on and --yes was not given; this binary also "
                       "serves MCP over stdio, so it never prompts on a pipe")
    output.write(f"About to run: {command}\n")
    output.flush()
    answer = stream.readline().strip().lower()
    return answer in {"y", "yes", "s", "si", "sí"}, f"answered {answer!r}"


def install(names=None, home=None, environ=None, suite_path=None, assume_yes=False,
            apply_changes=False, runner=None):
    """Install the requested clients that are missing. Nothing happens without --apply."""
    report = plan(names=names, home=home, environ=environ, suite_path=suite_path)
    report["mode"] = "apply" if apply_changes else "check"
    report["results"] = []
    runner = subprocess.run if runner is None else runner

    for record in report["clients"]:
        outcome = {"client": record["client"]}
        if record["installed"]:
            outcome["action"] = "noop"
            outcome["detail"] = f"already installed ({record['version'] or 'version unknown'})"
        elif not record["command"]:
            outcome["action"] = "blocked"
            outcome["detail"] = "the baseline declares no install recipe for this client"
        elif not record["manager_available"]:
            outcome["action"] = "blocked"
            outcome["detail"] = f"the recipe needs '{record['manager']}', which is not on PATH"
        elif not apply_changes:
            outcome["action"] = "would-install"
            outcome["detail"] = record["command"]
        else:
            allowed, why = _confirm(record, assume_yes)
            if not allowed:
                outcome["action"] = "declined"
                outcome["detail"] = why
            else:
                outcome.update(_run(record, runner))
        report["results"].append(outcome)

    report["installed"] = [item["client"] for item in report["results"]
                           if item.get("action") == "installed"]
    return report


def _run(record, runner):
    """Execute the recipe and verify the result instead of trusting the exit code alone."""
    argv = record["command"].split(" ")
    argv[0] = shutil.which(argv[0]) or argv[0]
    try:
        result = runner(argv, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"action": "failed", "detail": f"{type(exc).__name__} while running the recipe"}
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        return {"action": "failed",
                "detail": f"exit {result.returncode}: {tail[-1][:200] if tail else 'no output'}"}
    version = _version_of(record["client"] if record["client"] != "vscode" else "code",
                          ["--version"])
    if version is None:
        return {"action": "unverified",
                "detail": "the recipe succeeded but the program does not answer --version yet; "
                          "a new shell may be needed for PATH to pick it up"}
    return {"action": "installed", "detail": version}
