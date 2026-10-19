#!/usr/bin/env bash
# isc-ai workstation bootstrap for macOS and Linux.
#
#   curl -fsSL <pages-url>/install.sh | bash
#   curl -fsSL <pages-url>/install.sh | bash -s -- --yes --skip-wizard
#   ./bootstrap/install.sh --dry-run
#
# Installs everything an isc-ai workstation needs, skipping whatever is already there:
# git, uv, Node.js LTS, engram, the isc-ai CLI (and its extensions), the Perforce MCP
# server, the AI clients, the recommended MCP suite in every client, and then opens
# 'isc-ai wizard' for the IRIS environments and their credentials.
# Safe to run again: every step checks first. It never stores a credential, never contacts
# IRIS and never edits a project. The Perforce command-line client (p4) is only checked:
# download it from perforce.com if you need the Perforce MCP server.

set -u

# Kept equal to isc_ai.__version__ by tests/test_distribution.py.
ISC_AI_REF="v0.4.0"
GITLAB_HOST="gitlab.iscinternal.com"
CORE_SSH="git@gitlab.iscinternal.com:isc-ai-squad/isc-ai.git"
CORE_HTTPS="https://gitlab.iscinternal.com/isc-ai-squad/isc-ai.git"
P4MCP_SSH="ssh://git@gitlab.iscinternal.com/isc-ai-squad/perforce-mcp-server.git"
P4MCP_HTTPS="https://gitlab.iscinternal.com/isc-ai-squad/perforce-mcp-server.git"
P4MCP_REF="master"
ENGRAM_VERSION="3.0.0"
NVM_VERSION="v0.40.3"
SSH_KEYS_PAGE="https://gitlab.iscinternal.com/-/user_settings/ssh_keys"

ref=""; transport="ssh"; from=""; perforce_from=""; dry_run=""; yes=""
skip_perforce=""; skip_engram=""; skip_clients=""; skip_suite=""; skip_wizard=""

usage() {
  cat <<'EOF'
Usage: install.sh [--ref TAG] [--https] [--from DIR] [--perforce-from DIR] [--dry-run] [--yes]
                  [--skip-perforce] [--skip-engram] [--skip-clients] [--skip-suite] [--skip-wizard]
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --ref) ref="${2:-}"; shift ;;
    --https) transport="https" ;;
    --from) from="${2:-}"; shift ;;
    --perforce-from) perforce_from="${2:-}"; shift ;;
    --dry-run) dry_run="1" ;;
    --yes|-y) yes="1" ;;
    --skip-perforce) skip_perforce="1" ;;
    --skip-engram) skip_engram="1" ;;
    --skip-clients) skip_clients="1" ;;
    --skip-suite) skip_suite="1" ;;
    --skip-wizard) skip_wizard="1" ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done
[ -n "$ref" ] || ref="$ISC_AI_REF"
no_persist="${ISC_AI_BOOTSTRAP_NO_PERSIST:-}"

# Prompts read the terminal, not stdin: under 'curl | bash' stdin is this script.
interactive=""
if [ -z "$yes" ] && [ -r /dev/tty ] && { : < /dev/tty; } 2>/dev/null; then interactive="1"; fi

RESULTS=""
record() { RESULTS="${RESULTS}$1|$2|$3"$'\n'; printf '  [%s] %s: %s\n' "$1" "$2" "$3"; }
ok() { record OK "$1" "$2"; }
plan() { record PLAN "$1" "$2"; }
skip() { record SKIP "$1" "$2"; }
fail() { record FAIL "$1" "$2"; }
step() { printf '\n== %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }
add_path() { case ":$PATH:" in *":$1:"*) ;; *) [ -d "$1" ] && PATH="$1:$PATH" && export PATH ;; esac; }

os="$(uname -s)"; arch="$(uname -m)"
case "$arch" in x86_64|amd64) arch="amd64" ;; arm64|aarch64) arch="arm64" ;; esac
case "$os" in Darwin) os_name="darwin" ;; Linux) os_name="linux" ;; *) echo "Unsupported OS: $os" >&2; exit 1 ;; esac

pkg_install() {
  # pkg_install <command> <brew formula> <apt package> <dnf package>
  if have brew; then brew install "$2"
  elif have apt-get; then sudo apt-get update -qq && sudo apt-get install -y "$3"
  elif have dnf; then sudo dnf install -y "$4"
  else return 1; fi
}

printf '\nisc-ai bootstrap  (isc-ai %s, transport %s%s)\n' "$ref" "$transport" "${dry_run:+, DRY RUN}"
echo "Every step checks first; nothing that is already installed is touched."

# ------------------------------------------------------------------ prerequisites
step "git"
if have git; then ok git "already installed ($(command -v git))"
elif [ -n "$dry_run" ]; then plan git "would install git with the system package manager"
elif [ "$os_name" = "darwin" ] && ! have brew; then
  xcode-select --install 2>/dev/null
  fail git "accept the Xcode command line tools dialog, then run this again"
elif pkg_install git git git git; then ok git "installed"
else fail git "install git with your package manager, then run this again"; fi

step "uv (Python tool installer)"
add_path "$HOME/.local/bin"
if have uv; then ok uv "already installed ($(command -v uv))"
elif [ -n "$dry_run" ]; then plan uv "would run the official installer from https://astral.sh/uv/install.sh"
else
  [ -n "$no_persist" ] && export UV_NO_MODIFY_PATH=1
  curl -LsSf https://astral.sh/uv/install.sh | sh
  add_path "$HOME/.local/bin"
  if have uv; then ok uv "installed ($(command -v uv))"; else fail uv "installer failed"; fi
fi

step "Node.js LTS"
if have node; then ok node "already installed ($(command -v node))"
elif [ -n "$dry_run" ]; then plan node "would install Node.js LTS (Homebrew, or nvm $NVM_VERSION)"
elif have brew && brew install node; then ok node "installed with Homebrew"
else
  export NVM_DIR="$HOME/.nvm"
  if [ -n "$no_persist" ]; then export PROFILE=/dev/null; fi
  curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/$NVM_VERSION/install.sh" | bash
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh" && nvm install --lts
  if have node; then ok node "installed with nvm ($(node --version))"; else fail node "nvm could not install Node.js"; fi
fi

if [ -z "$skip_perforce" ]; then
  step "Perforce command-line client"
  if have p4; then ok p4 "already installed ($(command -v p4))"
  else skip p4 "not found: download 'Helix Command-Line Client' from perforce.com if you use the Perforce MCP server"; fi
fi

if [ -z "$skip_engram" ]; then
  step "engram $ENGRAM_VERSION (persistent memory MCP)"
  if have engram; then ok engram "already installed ($(command -v engram))"
  elif [ -n "$dry_run" ]; then plan engram "would install engram $ENGRAM_VERSION (Homebrew, or the release archive with SHA-256 check)"
  elif have brew && brew install gentleman-programming/tap/engram; then ok engram "installed with Homebrew"
  else
    asset="engram_${ENGRAM_VERSION}_${os_name}_${arch}.tar.gz"
    base="https://github.com/Gentleman-Programming/engram/releases/download/v$ENGRAM_VERSION"
    tmp="$(mktemp -d)"
    if curl -fsSL -o "$tmp/$asset" "$base/$asset" && curl -fsSL -o "$tmp/checksums.txt" "$base/checksums.txt"; then
      expected="$(grep " $asset\$" "$tmp/checksums.txt" | awk '{print $1}')"
      if have sha256sum; then actual="$(sha256sum "$tmp/$asset" | awk '{print $1}')"
      else actual="$(shasum -a 256 "$tmp/$asset" | awk '{print $1}')"; fi
      if [ -n "$expected" ] && [ "$expected" = "$actual" ]; then
        mkdir -p "$HOME/.local/bin" && tar -xzf "$tmp/$asset" -C "$tmp" && cp "$(find "$tmp" -name engram -type f | head -n1)" "$HOME/.local/bin/engram"
        chmod +x "$HOME/.local/bin/engram"
        ok engram "installed $ENGRAM_VERSION in $HOME/.local/bin (SHA-256 verified)"
      else fail engram "checksum mismatch for $asset: not installed"; fi
    else fail engram "download failed"; fi
    rm -rf "$tmp"
  fi
fi

finish() {
  printf '\n== Verification\n'
  if [ -z "$dry_run" ] && [ -z "${1:-}" ] && have isc-ai; then
    isc-ai audit >/dev/null 2>&1; code=$?
    case "$code" in
      0) ok audit "clean" ;;
      2) ok audit "no errors (warnings only: 'isc-ai audit' shows them)" ;;
      *) fail audit "errors found: run 'isc-ai audit' for the detail" ;;
    esac
  fi
  for t in git uv node npx p4 engram isc-ai perforce-mcp-server opencode code claude; do
    if have "$t"; then RESULTS="${RESULTS}OK|on PATH: $t|$(command -v "$t")"$'\n'
    else RESULTS="${RESULTS}-|on PATH: $t|not found"$'\n'; fi
  done
  echo
  printf '%s' "$RESULTS" | awk -F'|' '{ printf "  %-5s %-28s %s\n", $1, $2, $3 }'
  failed="$(printf '%s' "$RESULTS" | grep -c '^FAIL|' || true)"
  echo
  if [ -n "${1:-}" ]; then echo "STOPPED: $1 Fix the FAIL lines above and run the same command again."; exit 1
  elif [ "$failed" != "0" ]; then echo "Finished with $failed problem(s): fix the FAIL lines and run the same command again."; exit 1
  elif [ -n "$dry_run" ]; then echo "Dry run complete: nothing was changed."; exit 0
  else echo "Ready. Open a NEW terminal (PATH changed), then start your AI client inside your project folder."; exit 0; fi
}

if [ -z "$dry_run" ] && { ! have git || ! have uv; }; then finish "git and uv are required to continue."; fi

git_access() {
  GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20}" \
    git ls-remote "$1" "$2" 2>/dev/null | grep -Eq '^[0-9a-f]{40}[[:space:]]'
}

# ------------------------------------------------------------------ GitLab access
if [ -z "$from" ]; then
  step "Network access to $GITLAB_HOST"
  if ! { have getent && getent hosts "$GITLAB_HOST" >/dev/null; } && ! { have host && host "$GITLAB_HOST" >/dev/null 2>&1; } \
     && ! ping -c 1 -W 3 "$GITLAB_HOST" >/dev/null 2>&1 && ! curl -s -o /dev/null --max-time 10 "https://$GITLAB_HOST"; then
    fail network "$GITLAB_HOST does not resolve: connect the InterSystems VPN. A customer VPN does not reach the internal GitLab."
    finish "Cannot reach the internal GitLab."
  fi
  ok network "$GITLAB_HOST reachable"
  if [ "$transport" = "ssh" ]; then
    step "SSH access to GitLab"
    attempt=1; access=""
    while [ $attempt -le 3 ]; do
      if git_access "$CORE_SSH" "refs/tags/$ref"; then access="1"; break; fi
      key=""; for k in id_ed25519.pub id_ecdsa.pub id_rsa.pub; do [ -f "$HOME/.ssh/$k" ] && key="$HOME/.ssh/$k" && break; done
      if [ -n "$dry_run" ]; then plan ssh "GitLab rejects SSH: a key would be created or shown so you can add it"; access="dry"; break; fi
      if [ -z "$interactive" ]; then break; fi
      if [ -z "$key" ]; then
        echo "  No SSH key found. Creating one (choose a passphrase you will remember)."
        mkdir -p "$HOME/.ssh" && ssh-keygen -t ed25519 -C "$(whoami)@intersystems" -f "$HOME/.ssh/id_ed25519" </dev/tty
        key="$HOME/.ssh/id_ed25519.pub"
      fi
      echo "  Add this public key in GitLab ('Add new key'):"; echo; cat "$key"; echo
      if have open; then open "$SSH_KEYS_PAGE"; elif have xdg-open; then xdg-open "$SSH_KEYS_PAGE" >/dev/null 2>&1; else echo "  $SSH_KEYS_PAGE"; fi
      printf '  Press Enter when the key is saved (attempt %s of 3) ' "$attempt"; read -r _ </dev/tty
      attempt=$((attempt + 1))
    done
    if [ "$access" = "1" ]; then ok ssh "your SSH key reaches isc-ai-squad/isc-ai and tag $ref exists"
    elif [ -z "$access" ]; then fail ssh "GitLab rejects SSH. Add your public key at $SSH_KEYS_PAGE or run again with --https"; finish "SSH access to GitLab is not working."; fi
  fi
fi

# ------------------------------------------------------------------ isc-ai
step "isc-ai CLI $ref"
installed=""
have isc-ai && installed="$(isc-ai --version 2>/dev/null | awk '{print $2}')"
if [ -n "$installed" ] && [ "v$installed" = "$ref" ] && [ -z "$from" ]; then ok isc-ai "isc-ai $installed already installed"
elif [ -n "$dry_run" ]; then plan isc-ai "would install isc-ai from ${from:-GitLab $ref ($transport)} and run 'isc-ai setup --apply'"
else
  [ -n "$installed" ] && uv tool uninstall isc-ai
  log="${TMPDIR:-/tmp}/isc-ai-install.log"
  if [ -z "$from" ]; then
    tmp="$(mktemp -d)"
    if [ "$transport" = "ssh" ]; then url="$CORE_SSH"; https_flag=""; else url="$CORE_HTTPS"; https_flag="--https"; fi
    # shellcheck disable=SC2086
    { git clone --quiet --depth 1 --branch "$ref" "$url" "$tmp/isc-ai" && sh "$tmp/isc-ai/install.sh" --apply --remote --ref "$ref" $https_flag; } >"$log" 2>&1
    code=$?; rm -rf "$tmp"
  else
    sh "$from/install.sh" --apply >"$log" 2>&1; code=$?
  fi
  # install.sh prints the whole setup report: show only what a person needs.
  grep -E '^(Source:|Skipping extension|  extension |Installed [0-9]| \+ |isc-ai [0-9])' "$log" | sed 's/^/    /'
  [ $code -ne 0 ] && tail -n 30 "$log" | sed 's/^/    /'
  echo "    full log: $log"
  add_path "$(uv tool dir --bin 2>/dev/null)"
  if [ $code -eq 0 ] && have isc-ai; then ok isc-ai "$(isc-ai --version) installed, setup applied"
  else fail isc-ai "install.sh exited with $code"; finish "isc-ai did not install."; fi
fi
if [ -z "$dry_run" ] && [ -z "$no_persist" ]; then uv tool update-shell >/dev/null 2>&1 || true; fi

# ------------------------------------------------------------------ Perforce MCP
if [ -z "$skip_perforce" ]; then
  step "Perforce MCP server"
  if [ "$transport" = "ssh" ]; then p4url="$P4MCP_SSH"; else p4url="$P4MCP_HTTPS"; fi
  if have perforce-mcp-server; then ok perforce-mcp-server "already installed"
  elif [ -n "$dry_run" ]; then plan perforce-mcp-server "would install from ${perforce_from:-$p4url@$P4MCP_REF}"
  elif [ -z "$perforce_from" ] && [ -n "$from" ]; then skip perforce-mcp-server "local install (--from) without --perforce-from: not installed"
  elif [ -z "$perforce_from" ] && ! git_access "$p4url" "$P4MCP_REF"; then skip perforce-mcp-server "no access to the repository: ask the isc-ai-squad group for it"
  elif uv tool install "${perforce_from:-git+$p4url@$P4MCP_REF}" && have perforce-mcp-server; then ok perforce-mcp-server "installed"
  else fail perforce-mcp-server "uv could not install it"; fi
fi

# ------------------------------------------------------------------ AI clients and MCP suite
if [ -z "$skip_clients" ]; then
  step "AI clients (OpenCode, VS Code, Claude Code)"
  if [ -n "$dry_run" ]; then plan clients "would run 'isc-ai clients --apply'"
  elif [ -n "$interactive" ]; then
    if isc-ai clients --apply </dev/tty; then ok clients "checked"; else fail clients "isc-ai clients failed"; fi
  elif isc-ai clients --apply --yes >/dev/null; then ok clients "checked; missing clients installed"
  else fail clients "isc-ai clients failed"; fi
fi
if [ -z "$skip_suite" ]; then
  step "Recommended MCP suite (the 7 MCP servers, in every client)"
  selected=""; left=""
  for s in intersystems-iris perforce-mcp-server atlassian-cloud engram context7 markitdown playwright; do
    case "$s" in perforce-mcp-server) need=perforce-mcp-server ;; engram) need=engram ;; playwright) need=npx ;; *) need="" ;; esac
    if [ -z "$need" ] || have "$need"; then selected="$selected $s"; else left="$left $s"; fi
  done
  if [ -n "$dry_run" ]; then plan suite "would register:$selected"
  else
    failed_servers=""
    for s in $selected; do isc-ai suite --apply --server "$s" >/dev/null 2>&1 || failed_servers="$failed_servers $s"; done
    if [ -n "$failed_servers" ]; then fail suite "isc-ai suite failed for:$failed_servers"; else ok suite "registered:$selected"; fi
    [ -n "$left" ] && skip suite "not registered, command missing:$left"
  fi
fi

# ------------------------------------------------------------------ credentials
if [ -z "$skip_wizard" ]; then
  step "Credentials and IRIS environments (isc-ai wizard)"
  if [ -n "$dry_run" ]; then plan wizard "would open 'isc-ai wizard'"
  elif [ -z "$interactive" ]; then skip wizard "not interactive: run 'isc-ai wizard' in a terminal"
  elif isc-ai wizard </dev/tty; then ok wizard "completed"
  else skip wizard "closed early: run 'isc-ai wizard' again when ready"; fi
fi

finish ""
