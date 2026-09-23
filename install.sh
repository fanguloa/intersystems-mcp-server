#!/usr/bin/env sh
set -eu

repo=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

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

if [ "${1:-}" = "" ]; then
  printf '%s\n' "Dry run:" "  uv tool install --editable $repo" "  isc-ai setup --apply" \
    "Re-run with --apply to execute these local commands."
  exit 0
fi

uv tool install --editable "$repo"
isc-ai setup --apply
