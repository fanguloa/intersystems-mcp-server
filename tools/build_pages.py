"""Builds the GitLab Pages site that serves the one-line installers.

The site holds bootstrap/install.ps1 and bootstrap/install.sh exactly as committed, their
SHA-256 digests, and an index page with the commands to run. It refuses to publish a tag
that does not match the release the scripts install, so the page can never hand out an
installer for another version.

Usage: python tools/build_pages.py --ref vX.Y.Z [--out public]
"""
import argparse
import hashlib
import html
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("install.ps1", "install.sh")


def default_ref(text: str) -> str:
    match = re.search(r'ISC_AI_REF\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ""


def build(ref: str, out: Path, base_url: str = "<pages-url>") -> dict:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    digests = {}
    for name in SCRIPTS:
        data = (ROOT / "bootstrap" / name).read_bytes()
        found = default_ref(data.decode("ascii"))
        if found != ref:
            raise SystemExit(f"bootstrap/{name} installs {found or 'nothing'}, not {ref}: bump ISC_AI_REF first")
        (out / name).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        (out / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="ascii")
        digests[name] = digest
    url = html.escape(base_url.rstrip("/"))
    (out / "index.html").write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>isc-ai {html.escape(ref)}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;max-width:52rem;margin:3rem auto;padding:0 1rem;color:#222}}
code,pre{{font-family:Consolas,monospace;background:#f3f5f8;padding:.15rem .35rem;border-radius:4px}}
pre{{padding:.8rem 1rem;overflow:auto}}h1{{color:#00507d}}</style></head><body>
<h1>isc-ai {html.escape(ref)}</h1>
<p>One command installs everything an isc-ai workstation needs and skips what is already there.
Connect the InterSystems VPN first: the installer downloads isc-ai from the internal GitLab with your SSH key.</p>
<h2>Windows (PowerShell)</h2>
<pre>irm {url}/install.ps1 | iex</pre>
<p>With options: <code>&amp; ([scriptblock]::Create((irm {url}/install.ps1))) -DryRun</code></p>
<h2>macOS / Linux</h2>
<pre>curl -fsSL {url}/install.sh | bash</pre>
<p>With options: <code>curl -fsSL {url}/install.sh | bash -s -- --dry-run</code></p>
<h2>SHA-256</h2>
<pre>{digests['install.ps1']}  install.ps1
{digests['install.sh']}  install.sh</pre>
<p>Maintainer: Felipe Angulo &lt;<a href="mailto:felipe.angulo@intersystems.com">felipe.angulo@intersystems.com</a>&gt; &middot;
<a href="https://gitlab.iscinternal.com/isc-ai-squad/isc-ai/-/issues">issues</a></p>
</body></html>
""", encoding="utf-8")
    return digests


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", required=True)
    parser.add_argument("--out", default=str(ROOT / "public"))
    parser.add_argument("--base-url", default="<pages-url>")
    args = parser.parse_args()
    digests = build(args.ref, Path(args.out), args.base_url)
    for name, digest in digests.items():
        print(f"{digest}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
