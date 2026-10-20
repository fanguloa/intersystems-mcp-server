"""Installation smoke test: build the wheel, install it in a throwaway venv and drive the CLI.

The unit suite imports the package from the source tree, so it cannot see what a user gets
from `uv tool install`: a resource missing from the wheel, a broken entry point or a
command that only works inside the repository. This script checks the installed artifact,
offline, with HOME and every config directory redirected to a temporary sandbox, so it never
reads or writes the real AI-client configuration and never contacts a server.

    python tools/smoke_install.py                 # builds with 'uv build', then checks
    python tools/smoke_install.py --wheel dist/isc_ai-0.5.0-py3-none-any.whl

Exit code 0 when every check passes; the report lists each check either way.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"


def repo_version():
    text = (ROOT / "isc_ai" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


class Smoke:
    def __init__(self, work: Path):
        self.work = work
        self.results: list[tuple[bool, str, str]] = []
        self.home = work / "home"
        self.project = work / "project"
        for path in (self.home, self.project, self.home / "AppData" / "Roaming", self.home / "AppData" / "Local",
                     self.home / ".config"):
            path.mkdir(parents=True, exist_ok=True)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("ISC_AI_", "IRIS_MCP_", "IRIS_"))}
        self.env.update({
            "HOME": str(self.home), "USERPROFILE": str(self.home),
            "APPDATA": str(self.home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(self.home / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1",
        })
        self.exe: Path | None = None

    def check(self, ok: bool, name: str, detail: str = ""):
        self.results.append((bool(ok), name, detail))
        return ok

    def run(self, *args, stdin: str | None = None, cwd: Path | None = None):
        return subprocess.run([str(self.exe), *args], input=stdin, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=self.env,
                              cwd=str(cwd or self.project), timeout=120)

    def install(self, wheel: Path):
        target = self.work / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(target)
        python = target / ("Scripts/python.exe" if WINDOWS else "bin/python")
        result = subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--no-index", "--no-deps",
                                 str(wheel)], capture_output=True, text=True)
        self.check(result.returncode == 0, "wheel installs offline with no dependencies",
                   result.stderr.strip()[-400:])
        self.exe = target / ("Scripts/isc-ai.exe" if WINDOWS else "bin/isc-ai")
        self.check(self.exe.is_file(), "console script isc-ai is installed", str(self.exe))
        self.site = Path(subprocess.run([str(python), "-c", "import isc_ai, pathlib; print(pathlib.Path(isc_ai.__file__).parent)"],
                                        capture_output=True, text=True, cwd=str(self.work)).stdout.strip())

    def snapshot(self):
        # `clients`/`audit` run `opencode --version`, and OpenCode writes its own log while
        # answering: that file belongs to the probed binary, not to isc-ai.
        return sorted(str(path.relative_to(self.home)) for path in self.home.rglob("*")
                      if path.is_file() and "opencode" + os.sep + "log" not in str(path))

    def checks(self, version: str):
        result = self.run("--version")
        self.check(result.stdout.strip().startswith(f"isc-ai {version} "), "--version reports the repository version",
                   result.stdout.strip())

        result = self.run("--help")
        self.check(result.returncode == 0, "--help exits 0", result.stderr[-300:])
        block = result.stdout.split("commands:", 1)[-1].split("examples:", 1)[0]
        commands = re.findall(r"^ {4}([a-z][a-z-]+)\s", block, re.MULTILINE)
        self.check(len(commands) >= 10, "--help lists the commands", ", ".join(commands))
        for command in commands:
            result = self.run(command, "-h")
            self.check(result.returncode == 0, f"{command} -h exits 0", result.stderr[-300:])
        self.check(self.run("--lang", "es", "--help").returncode == 0, "--lang es --help exits 0")

        before = self.snapshot()
        result = self.run("setup")
        self.check(result.returncode == 0 and json.loads(result.stdout).get("mode") == "check",
                   "setup without --apply is a plan", result.stderr[-300:])
        for args in (("audit",), ("suite",), ("clients",), ("agents",), ("migrate",), ("secrets", "list")):
            result = self.run(*args)
            try:
                json.loads(result.stdout)
                parsed = True
            except ValueError:
                parsed = False
            self.check(parsed and result.returncode in (0, 1, 2), f"{' '.join(args)} prints a JSON report",
                       f"rc={result.returncode} {result.stderr[-200:]}")
        self.check(self.snapshot() == before, "read-only commands write nothing under HOME",
                   ", ".join(sorted(set(self.snapshot()) - set(before))))

        result = self.run("init")
        self.check(result.returncode == 0 and (self.project / ".isc-ai" / "config.json").is_file(),
                   "init creates .isc-ai/config.json", result.stdout.strip()[-200:])
        result = self.run("config", "show")
        self.check(result.returncode == 0 and "canonical_base" in json.loads(result.stdout), "config show reads it back")
        result = self.run("doctor")
        self.check(result.returncode in (0, 1) and json.loads(result.stdout).get("project_config_present") is True,
                   "doctor resolves the project", f"rc={result.returncode}")
        nested = self.project / "a" / "b"
        nested.mkdir(parents=True)
        result = self.run("doctor", cwd=nested)
        self.check(Path(json.loads(result.stdout)["project_dir"]).resolve() == self.project.resolve(),
                   "doctor finds the project from a subdirectory")

        requests = "\n".join(json.dumps(item) for item in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )) + "\n"
        result = self.run("mcp", stdin=requests)
        responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip().startswith("{")]
        by_id = {item.get("id"): item for item in responses}
        self.check(by_id.get(1, {}).get("result", {}).get("serverInfo", {}).get("version") == version,
                   "mcp answers initialize with the package version", result.stderr[-300:])
        tools = by_id.get(2, {}).get("result", {}).get("tools", [])
        self.check(len(tools) > 10, "mcp lists its tools", f"{len(tools)} tools")

        manifest = json.loads((self.site / "resources" / "harness" / "manifest.json").read_text(encoding="utf-8"))
        missing = []
        for entry in manifest.get("files", manifest.get("entries", [])):
            path = self.site / "resources" / "harness" / entry["source"]
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                missing.append(entry["source"])
        self.check(not missing, "every harness file is in the wheel with its manifest hash", ", ".join(missing[:5]))
        self.check(any((self.site / "logigen" / "templates").glob("*.xml")), "logi templates are in the wheel")


def build(out: Path) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required to build the wheel (or pass --wheel).")
    subprocess.run([uv, "build", "--wheel", "--out-dir", str(out), str(ROOT)], check=True)
    return next(out.glob("isc_ai-*.whl"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--wheel", type=Path, help="wheel to check (default: build one with uv)")
    parser.add_argument("--keep", action="store_true", help="keep the sandbox directory for inspection")
    args = parser.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="isc-ai-smoke-"))
    try:
        wheel = args.wheel.resolve() if args.wheel else build(work / "dist")
        version = repo_version()
        smoke = Smoke(work)
        smoke.check(wheel.name.startswith(f"isc_ai-{version}-"), "wheel name carries the repository version", wheel.name)
        smoke.install(wheel)
        if smoke.exe and smoke.exe.is_file():
            smoke.checks(version)
        failed = [item for item in smoke.results if not item[0]]
        for ok, name, detail in smoke.results:
            line = f"{'PASS' if ok else 'FAIL'}  {name}"
            if detail and not ok:
                line += f"  ({detail})"
            print(line)
        print(f"\n{len(smoke.results) - len(failed)}/{len(smoke.results)} checks passed")
        return 1 if failed else 0
    finally:
        if args.keep:
            print(f"sandbox kept at {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
