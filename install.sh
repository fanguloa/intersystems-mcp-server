#!/usr/bin/env sh
set -eu

repo=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# Extensions this installer knows how to probe and, if reachable, install.
# Baked in on purpose: no URL is ever discovered at runtime, only probed.
EXTENSIONS="isc-ai-technical:https://gitlab.iscinternal.com/isc-ai-squad/isc-ai-technical.git"
EXTENSION_ACCESS_URL="https://gitlab.iscinternal.com/groups/isc-ai-squad/technical-users"

usage() {
  printf '%s\n' "Usage: ./install.sh [--apply]" \
    "  no arguments  Print the dry-run plan." \
    "  --apply       Install this local repository and apply isc-ai setup."
}

if [ "$#" -gt 1 ]; then
  usage >&2
  exit 2
fi

case "${1:-}" in
  ""|--apply) ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    printf 'Unknown argument: %s\n' "$1" >&2
    usage >&2
    exit 2
    ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required and was not found on PATH." >&2
  exit 1
fi

# Pre-flight extension probing. Uses the same git credentials as any other
# clone; never a separate API token. A 403/404 skips just that extension and
# keeps going: an unreachable extension repo never fails the whole install.
# An authentication failure stops here, loudly, with a login instruction --
# it means the user's git credentials cannot even be evaluated yet.
probe_extension() {
  # $1=url. Echoes one of: ok / not-found / forbidden / auth-failed
  # GIT_TERMINAL_PROMPT=0: fail immediately instead of hanging on a
  # credential prompt when no cached git credentials are available.
  output=$(GIT_TERMINAL_PROMPT=0 git ls-remote "$1" HEAD 2>&1) && { printf 'ok\n'; return 0; }
  case "$output" in
    *"Authentication failed"*|*"could not read Username"*|*"terminal prompts disabled"*|*"401"*)
      printf 'auth-failed\n' ;;
    *"not found"*|*"Repository not found"*|*"404"*)
      printf 'not-found\n' ;;
    *)
      printf 'forbidden\n' ;;
  esac
  return 1
}

report_extensions() {
  # $1: "1" to actually install a reachable extension, "" to only report.
  apply="$1"
  for entry in $EXTENSIONS; do
    ext_name=${entry%%:*}
    ext_url=${entry#*:}
    result=$(probe_extension "$ext_url") || true
    case "$result" in
      ok)
        if [ "$apply" = "1" ]; then
          printf 'Installing extension %s...\n' "$ext_name"
          uv tool install --with "git+${ext_url}@main" isc-ai
        else
          printf '  extension %s: reachable, would install with --apply\n' "$ext_name"
        fi
        ;;
      "not-found")
        printf 'Skipping extension %s: repository not found.\n' "$ext_name" >&2
        ;;
      forbidden)
        printf 'Skipping extension %s: no access (forbidden). Request access: %s\n' \
          "$ext_name" "$EXTENSION_ACCESS_URL" >&2
        ;;
      "auth-failed")
        printf 'Cannot reach extension %s: log in to GitLab first (run "git ls-remote %s" once to authenticate), then re-run this installer.\n' \
          "$ext_name" "$ext_url" >&2
        exit 1
        ;;
    esac
  done
}

if [ "${1:-}" = "" ]; then
  printf '%s\n' "Dry run:" "  uv tool install --editable $repo" "  isc-ai setup --apply" \
    "Re-run with --apply to execute these local commands."
  report_extensions ""
  exit 0
fi

uv tool install --editable "$repo"
isc-ai setup --apply
report_extensions "1"

