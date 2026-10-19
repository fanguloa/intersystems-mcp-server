"""Runs bootstrap/install.ps1 end to end in an isolated sandbox and records every run.

HOME, USERPROFILE, APPDATA, LOCALAPPDATA and XDG_CONFIG_HOME point to an empty folder and
only operating-system variables survive. uv, engram and isc-ai are removed from PATH so the
bootstrap has to install them for real; git, Node.js, winget, p4 and the AI clients stay
visible so nothing is installed machine-wide. ISC_AI_BOOTSTRAP_NO_PERSIST and
UV_NO_MODIFY_PATH keep the persistent user PATH untouched.

Usage: python tools/smoke_bootstrap.py --from <clone of the release tag> [--out transcript.json]
Windows only. It downloads uv and engram from their official release URLs.
"""
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "bootstrap" / "install.ps1"
ENV_ALLOW = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "OS",
             "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL", "PROCESSOR_REVISION",
             "NUMBER_OF_PROCESSORS", "USERNAME", "USERDOMAIN", "COMPUTERNAME", "PROGRAMFILES",
             "PROGRAMFILES(X86)", "PROGRAMW6432", "PROGRAMDATA", "COMMONPROGRAMFILES",
             "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432", "ALLUSERSPROFILE", "PUBLIC", "PSMODULEPATH",
             "DRIVERDATA", "LOGONSERVER", "SESSIONNAME"}
HIDDEN_TOOLS = ("uv.exe", "isc-ai.exe", "engram.exe", "perforce-mcp-server.exe")


def force_remove(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


class Sandbox:
    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.shims = root / "shims"
        if root.exists():
            shutil.rmtree(root, onexc=force_remove)
        for p in (self.home / "AppData" / "Roaming", self.home / "AppData" / "Local", self.home / ".config",
                  self.shims):
            p.mkdir(parents=True)
        self.env = self._env()

    def _env(self):
        env = {k: v for k, v in os.environ.items() if k.upper() in ENV_ALLOW}
        kept, shimmed = [], []
        for entry in os.environ["PATH"].split(os.pathsep):
            if not entry:
                continue
            hidden = [t for t in HIDDEN_TOOLS if (Path(entry) / t).exists()]
            if not hidden:
                kept.append(entry)
                continue
            # A directory that also holds an AI client keeps only that client, through a shim.
            for name in ("claude.exe", "opencode.exe"):
                if (Path(entry) / name).exists():
                    (self.shims / (Path(name).stem + ".cmd")).write_text(
                        f'@"{Path(entry) / name}" %*\r\n', encoding="ascii")
                    shimmed.append(name)
        self.shimmed = shimmed
        env.update({
            "HOME": str(self.home), "USERPROFILE": str(self.home), "HOMEDRIVE": self.home.drive,
            "HOMEPATH": str(self.home)[2:],
            "APPDATA": str(self.home / "AppData" / "Roaming"), "LOCALAPPDATA": str(self.home / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "UV_CACHE_DIR": str(Path(os.environ["LOCALAPPDATA"]) / "uv" / "cache"),
            "UV_PYTHON_INSTALL_DIR": str(Path(os.environ["APPDATA"]) / "uv" / "python"),
            "UV_NO_MODIFY_PATH": "1", "ISC_AI_BOOTSTRAP_NO_PERSIST": "1",
            "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1",
            "PATH": os.pathsep.join([str(self.shims)] + kept),
        })
        return env

    def run(self, command: str, cwd: Path | None = None, timeout=1800):
        script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
                  f"Set-Location -LiteralPath '{cwd or self.root}'\n$global:LASTEXITCODE = 0\n"
                  f"& {{ {command} }} *>&1 | Out-String -Stream -Width 200\n"
                  "exit $LASTEXITCODE\n")
        with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig") as fh:
            fh.write(script)
            path = fh.name
        try:
            started = time.time()
            proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                   "-File", path], cwd=str(cwd or self.root), env=self.env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
            lines = proc.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")
            while lines and not lines[-1].strip():
                lines.pop()
            while lines and not lines[0].strip():
                lines.pop(0)
            return {"command": command, "exit_code": proc.returncode, "seconds": round(time.time() - started, 1),
                    "cwd": str(cwd or self.root), "output": "\n".join(l.rstrip() for l in lines)}
        finally:
            os.unlink(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="source", default="", help="clone of the release tag (offline mode)")
    parser.add_argument("--perforce-from", default="", help="clone of perforce-mcp-server (offline mode)")
    parser.add_argument("--ssh-key", default="", help="private key for GitLab, read in place (remote mode)")
    parser.add_argument("--out", default=str(ROOT / "dist" / "bootstrap-smoke.json"))
    parser.add_argument("--root", default=str(Path(tempfile.gettempdir()) / "isc-ai-bootstrap-smoke"))
    args = parser.parse_args()
    if os.name != "nt":
        sys.exit("smoke_bootstrap.py is Windows-only")
    if bool(args.source) == bool(args.ssh_key):
        sys.exit("choose one mode: --from <clone> (offline) or --ssh-key <key> (remote, GitLab reachable)")

    box = Sandbox(Path(args.root))
    script = str(BOOTSTRAP)
    remote = bool(args.ssh_key)
    if remote:
        # The key stays where it is; only the sandbox known_hosts is written.
        key = Path(args.ssh_key).resolve().as_posix()
        known = (box.home / ".ssh" / "known_hosts").as_posix()
        (box.home / ".ssh").mkdir()
        ssh_command = (f"C:/Windows/System32/OpenSSH/ssh.exe -i {key} -o IdentitiesOnly=yes -o BatchMode=yes "
                       f"-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={known}")
        source = ""
    else:
        source = f" -From '{Path(args.source).resolve()}'"
        if args.perforce_from:
            source += f" -PerforceFrom '{Path(args.perforce_from).resolve()}'"
    with_key = f"$env:GIT_SSH_COMMAND = '{ssh_command}'; " if remote else ""
    steps = []
    checks = []

    def step(step_id, *commands):
        records = [box.run(c) for c in commands]
        steps.append({"id": step_id, "commands": records})
        for r in records:
            print(f"[{step_id}] exit={r['exit_code']} {r['seconds']}s  {r['command'][:100]}")
        return records

    def check(ok, name):
        checks.append((bool(ok), name))
        print(("  PASS " if ok else "  FAIL ") + name)

    r = step("b1-before", "foreach ($t in 'uv','isc-ai','engram') { \"$t -> $([bool](Get-Command $t -ErrorAction SilentlyContinue))\" }")[0]
    check("uv -> False" in r["output"] and "isc-ai -> False" in r["output"], "sandbox starts without uv, isc-ai and engram")

    r = step("b2-dry-run", f"& '{script}' -DryRun -SkipWizard")[0]
    check("[PLAN] uv" in r["output"] and "[PLAN] engram" in r["output"], "dry run plans uv and engram")
    check(not (box.home / ".local").exists() and not (box.home / "AppData" / "Local" / "engram").exists(),
          "dry run writes nothing")

    r = step("b3-no-access", f"& '{script}' -Yes -SkipWizard -SkipEngram")[0]
    gitlab_down = "does not resolve" in r["output"]
    stopped = r["exit_code"] == 1 and "STOPPED" in r["output"]
    if gitlab_down:
        check(stopped, "without GitLab access it stops with a clear reason and exit 1")
    else:
        check(stopped and "[FAIL] ssh" in r["output"], "without an SSH key it stops at the SSH step with exit 1")

    r = step("b4-install", with_key + f"& '{script}'{source} -Yes -SkipWizard")[0]
    check(r["exit_code"] == 0, f"full install ({'GitLab' if remote else 'local clone'}) exits 0")
    expected = ["[OK] uv: installed", "[OK] engram: installed", "[OK] isc-ai: isc-ai", "[OK] suite", "[OK] audit"]
    if remote:
        expected += ["[OK] network", "[OK] ssh"]
    if remote or args.perforce_from:
        expected.append("[OK] perforce-mcp-server: installed")
    for item in expected:
        check(item in r["output"], f"install reports {item}")

    new_terminal = "$env:Path = \"$env:USERPROFILE\\.local\\bin;$env:LOCALAPPDATA\\engram\\bin;$env:Path\"; "
    r = step("b5-again", new_terminal + with_key + f"& '{script}'{source} -Yes -SkipWizard")[0]
    again = ["[OK] uv: already installed", "[OK] engram: already installed"]
    if remote:
        again += ["[OK] isc-ai: isc-ai 0.4.0 already installed", "[OK] perforce-mcp-server: already installed"]
    check(r["exit_code"] == 0 and all(a in r["output"] for a in again), "second run is idempotent")

    r = step("b6-scriptblock",
             f"& ([scriptblock]::Create((Get-Content -Raw '{script}'))) -DryRun -SkipWizard -SkipPerforce; 'after-bootstrap'")[0]
    check("after-bootstrap" in r["output"], "scriptblock invocation (irm | iex style) never closes the session")

    r = step("b7-verify",
             "$env:Path = \"$env:USERPROFILE\\.local\\bin;$env:LOCALAPPDATA\\engram\\bin;$env:Path\"; "
             "# a new terminal: the user PATH would hold these two directories\n"
             "isc-ai --version; (Get-Command uv, engram, isc-ai).Source; "
             "isc-ai audit | ConvertFrom-Json | Select-Object -ExpandProperty summary | Format-List")
    check("isc-ai 0.4.0" in r[0]["output"], "isc-ai 0.4.0 on PATH")

    report = {"generated": datetime.now().isoformat(timespec="seconds"), "sandbox_root": str(box.root),
              "home": str(box.home), "shimmed_clients": box.shimmed, "gitlab_reachable": not gitlab_down,
              "mode": "remote" if remote else "local",
              "steps": steps, "checks": [{"ok": ok, "name": n} for ok, n in checks]}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [n for ok, n in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed -> {args.out}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
