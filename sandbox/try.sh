#!/usr/bin/env bash
# Playground: a temp home holding a copy of your Claude setup, with bondi ready to run.
# Your real ~/.claude is never written. Exit the shell to delete the playground (KEEP=1 keeps it).
# Non-interactive smoke run: sandbox/try.sh -c 'bondi _scan --claude-dir ~/.claude'
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
REAL="$HOME"
T="$(mktemp -d "${TMPDIR:-/tmp}/bondi-try.XXXXXX")"
T="$(cd "$T" && pwd -P)"
H="$T/home"
mkdir -p "$H" "$T/machine-b" "$T/remotes" "$T/zdot"
cleanup() { [ "${KEEP:-}" = 1 ] && echo "kept: $T" || rm -rf "$T"; }
trap cleanup EXIT

(cd "$here" && uv sync --quiet)

copy() { [ -e "$REAL/$1" ] || [ -L "$REAL/$1" ] || return 0; mkdir -p "$H/$(dirname "$1")"; cp -pR "$REAL/$1" "$H/$1"; }
for f in CLAUDE.md settings.json keybindings.json hooks memory skills rules agents commands; do copy ".claude/$f"; done
for d in "$REAL"/.claude/projects/*/memory; do [ -d "$d" ] && copy "${d#"$REAL"/}"; done
for d in "$REAL"/.claude-*; do [ -d "$d" ] || continue; for f in settings.json skills memory; do copy "${d#"$REAL"/}/$f"; done; done
for f in .agents .gitconfig .zshrc .zprofile .ssh/config; do copy "$f"; done
# Decoys: the playground proves bondi never picks these up.
echo '{"decoy": true}' >"$H/.claude/.claude.json"
echo '{}' >"$H/.claude/history.jsonl"

cat >"$T/zdot/.zshrc" <<EOF
export HOME="$H"
unset CLAUDE_CONFIG_DIR XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false
bondi() { "$here/.venv/bin/bondi" "\$@"; }
# mkremote personal -> prints a local bare repo path you can pass to --remote or add
mkremote() { git init -q --bare -b main "$T/remotes/\$1.git" && echo "$T/remotes/\$1.git"; }
# b <command> -> run a command as a second, empty machine (its own home)
b() { ( export HOME="$T/machine-b"; unset CLAUDE_CONFIG_DIR XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME; "\$@" ) }
PROMPT='%F{green}(bondi playground)%f %~ %# '
cd "\$HOME"
EOF

if [ "${1:-}" = "-c" ]; then
  ZDOTDIR="$T/zdot" zsh -c "source '$T/zdot/.zshrc'; ${2:?}"
  exit
fi

cat <<EOF

bondi playground
  home (copy of your setup):  $H
  second machine (empty):     $T/machine-b
  your real ~/.claude is not touched.

Try:
  bondi                                   # where things stand
  bondi export personal                   # edit the suggested bondi.toml, save, done
  bondi export personal --remote \$(mkremote personal)  # same, and push to a local test remote
  b bondi add $T/remotes/personal.git      # put it on the empty second machine
  b ls ~/.claude                          # see what arrived there
  echo '- new fact' > ~/.claude/memory/x.md && bondi sync && b bondi sync
  bondi ; bondi sync --dry-run

Note: paths under your real home show as /Users/... here, because this home is a temp folder.
Exit the shell to delete everything.

EOF
ZDOTDIR="$T/zdot" zsh -i
