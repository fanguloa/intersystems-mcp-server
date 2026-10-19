#!/usr/bin/env sh
# Install or upgrade the isc-ai CLI with uv, then apply the local AI-client setup.
#
# Dry run by default: prints the exact commands and changes nothing. --apply runs them.
# Two sources, chosen automatically:
#   local   this script sits in a clone (pyproject.toml next to it): editable install of
#           that clone, so a 'git pull' is all an upgrade needs.
#   remote  anywhere else (or --remote): installs the release tag --ref straight from
#           GitLab with the git credentials you already use (no clone, no API token).
# It never installs uv, Node or an AI client, never edits PATH or profiles and never
# contacts IRIS.
set -eu

# Default release for a remote install. Kept equal to isc_ai.__version__ by
# tests/test_distribution.py, so a release bumps both in the same commit.
ISC_AI_REF="v0.4.1"
CORE_URL_SSH="git+ssh://git@gitlab.iscinternal.com/isc-ai-squad/isc-ai.git"
CORE_URL_HTTPS="git+https://gitlab.iscinternal.com/isc-ai-squad/isc-ai.git"

# Extensions this installer knows how to probe and, if reachable, install.
# Baked in on purpose: no URL is ever discovered at runtime, only probed. Each one is
# probed and installed over the same transport as the core (name|ssh url|https url), so
# an SSH-only user is never sent to an HTTPS credential prompt.
EXTENSIONS="isc-ai-technical|ssh://git@gitlab.iscinternal.com/isc-ai-squad/isc-ai-technical.git|https://gitlab.iscinternal.com/isc-ai-squad/isc-ai-technical.git"
EXTENSION_ACCESS_URL="https://gitlab.iscinternal.com/groups/isc-ai-squad/technical-users"
# pre-first-release: set this to the first published tag once isc-ai-technical
# cuts one (MANUAL-2); until then it installs whatever HEAD of the default
# branch currently is, same behaviour as before this ref was pulled into a
# named, grep-able variable.
ISC_AI_TECHNICAL_REF="main"

usage() {
  printf '%s\n' "Usage: ./install.sh [--apply] [--remote] [--ref TAG] [--https]" \
    "  no arguments  Print the dry-run plan." \
    "  --apply       Install and apply isc-ai setup." \
    "  --remote      Install from GitLab even inside a clone." \
    "  --ref TAG     Release tag or branch for a remote install (default $ISC_AI_REF)." \
    "  --https       Use HTTPS instead of SSH for a remote install."
}

apply=""
remote=""
ref=""
transport="ssh"
core_url="$CORE_URL_SSH"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) apply="1" ;;
    --remote) remote="1" ;;
    --https)
      transport="https"
      core_url="$CORE_URL_HTTPS"
      ;;
    --ref)
      if [ "$#" -lt 2 ]; then
        usage >&2
        exit 2
      fi
      ref="$2"
      shift
      ;;
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
  shift
done

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required and was not found on PATH. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

repo=""
case "$0" in
  */install.sh|install.sh) repo=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) ;;
esac
if [ -n "$repo" ] && [ -f "$repo/pyproject.toml" ] && [ -z "$remote" ]; then
  set -- --editable "$repo"
  source_label="local clone $repo"
else
  [ -n "$ref" ] || ref="$ISC_AI_REF"
  set -- "${core_url}@${ref}"
  source_label="GitLab $ref"
fi

# Pre-flight extension probing, before anything is installed. Uses the same git
# credentials as any other clone; never a separate API token. A 403/404 skips
# just that extension and keeps going: an unreachable extension repo never fails
# the whole install. An authentication failure stops here, loudly, with a login
# instruction -- it means the user's git credentials cannot even be evaluated yet.
probe_extension() {
  # $1=url $2=ref. Echoes one of: ok / no-ref / not-found / forbidden / auth-failed
  # GIT_TERMINAL_PROMPT=0, GCM_INTERACTIVE=never and ssh BatchMode: fail
  # immediately instead of hanging on a credential prompt when no cached git
  # credentials are available (an existing GIT_SSH_COMMAND is respected).
  ssh_command=${GIT_SSH_COMMAND:-ssh -o BatchMode=yes}
  if output=$(GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never GIT_SSH_COMMAND="$ssh_command" \
    git ls-remote "$1" "$2" 2>&1); then
    # A reachable repository without that branch or tag (e.g. still empty) lists
    # nothing: installing it would fail the whole uv call, core included.
    if printf '%s\n' "$output" | grep -Eq '^[0-9a-f]{40}[[:space:]]'; then
      printf 'ok\n'
      return 0
    fi
    printf 'no-ref\n'
    return 1
  fi
  case "$output" in
    *"Authentication failed"*|*"could not read Username"*|*"terminal prompts disabled"*|*"401"*|*"Permission denied"*|*"Host key verification failed"*)
      printf 'auth-failed\n' ;;
    *"not found"*|*"Repository not found"*|*"404"*)
      printf 'not-found\n' ;;
    *)
      printf 'forbidden\n' ;;
  esac
  return 1
}

with_args=""
report_extensions() {
  for entry in $EXTENSIONS; do
    ext_name=${entry%%|*}
    ext_urls=${entry#*|}
    if [ "$transport" = "https" ]; then
      ext_url=${ext_urls#*|}
    else
      ext_url=${ext_urls%%|*}
    fi
    result=$(probe_extension "$ext_url" "$ISC_AI_TECHNICAL_REF") || true
    case "$result" in
      ok)
        printf '  extension %s: reachable, installed together with isc-ai\n' "$ext_name"
        with_args="$with_args git+${ext_url}@${ISC_AI_TECHNICAL_REF}"
        ;;
      "no-ref")
        printf "Skipping extension %s: reachable, but '%s' is not published there yet.\n" \
          "$ext_name" "$ISC_AI_TECHNICAL_REF" >&2
        ;;
      "not-found")
        printf 'Skipping extension %s: not found or no access (GitLab returns 404 for both). Request access: %s\n' \
          "$ext_name" "$EXTENSION_ACCESS_URL" >&2
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

printf 'Source: %s\n' "$source_label"
report_extensions
# The extensions ride on the same source as the core: a bare 'isc-ai' would be
# resolved from the public package index, where that name is not ours.
for extension in $with_args; do
  set -- "$@" --with "$extension"
done

if [ -z "$apply" ]; then
  printf '%s\n' "Dry run:" "  uv tool install $*" "  isc-ai setup --apply" \
    "Re-run with --apply to execute these local commands."
  exit 0
fi

uv tool install "$@"
# The fresh executable may not be on PATH in this shell yet.
bin_dir=$(uv tool dir --bin)
isc_ai="$bin_dir/isc-ai"
[ -x "$isc_ai" ] || isc_ai="isc-ai"
"$isc_ai" setup --apply
if ! command -v isc-ai >/dev/null 2>&1; then
  printf '%s is not on PATH: run "uv tool update-shell" and open a new terminal.\n' "$bin_dir" >&2
fi
"$isc_ai" --version
