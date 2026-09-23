"""The packaging gate: what may never enter the bundle.

One definition, used by the tools that add files and by the test that keeps whatever is
already packaged honest. The bundle installs on other people's machines, so three kinds
of content are disqualifying:

  * a credential-shaped value, told apart from a documented label by shape: a password
    mixes letters with digits or is long with mixed case, while `size:exception` and
    `auto-chain` are prose and live inside backticks
  * a machine-specific home directory, which is either wrong or someone else's
  * a client site identifier, because one client's decisions are not shared doctrine
"""

import re

CREDENTIAL = re.compile(r"\b([a-z][a-z0-9._-]{2,}):([^\s|<>\"'/`]{4,})\b")
CREDENTIAL_SAFE = re.compile(r"(?i)(https?:|isfs:|file:|env:|\{env|\$\{|\{user\}|user:pass|example|schema)")
IN_BACKTICKS = re.compile(r"`[^`]*`")
MACHINE_PATH = re.compile(r"(?i)([A-Za-z]:[\\/]Users[\\/][^\\/\s]+|/home/[^/\s]+|/Users/[^/\s]+)")
CLIENT_NAMES = re.compile(r"(?i)\b(achs|clua|clme|clrs|clcb|cluc|sdsr|sdcq|sdor|sdtl|sdvn|smoc)\w*\b")


def looks_like_a_password(value):
    """Tell a real secret from prose tokens such as `size:exception` or `auto-chain`."""
    has_letter = any(char.isalpha() for char in value)
    has_digit = any(char.isdigit() for char in value)
    mixed_case = value != value.lower() and value != value.upper()
    return has_letter and (has_digit or (len(value) >= 8 and mixed_case))


def gate(relative, text):
    """Return the reasons this content must not be packaged. Empty means it may ship."""
    problems = []
    for number, line in enumerate(text.splitlines(), 1):
        bare = IN_BACKTICKS.sub("``", line)
        match = CREDENTIAL.search(bare)
        if match and looks_like_a_password(match.group(2)) and not CREDENTIAL_SAFE.search(bare):
            problems.append(f"{relative}:{number} credential-shaped value")
        machine = MACHINE_PATH.search(line)
        if machine:
            problems.append(f"{relative}:{number} machine-specific path {machine.group(0)!r}")
        client = CLIENT_NAMES.search(line)
        if client:
            problems.append(f"{relative}:{number} client identifier {client.group(0)!r}")
    return problems
