#!/usr/bin/env bash
# Two-machine scenario with synthetic data. Runs the same on macOS and Linux.
# Needs: SANDBOX (an empty folder), BONDI (the command to run bondi).
set -euo pipefail
: "${SANDBOX:?}" "${BONDI:?}"
export GIT_CONFIG_GLOBAL="$SANDBOX/gitconfig" GIT_CONFIG_NOSYSTEM=1
printf '[user]\n\tname = Sandbox\n\temail = sandbox@example.invalid\n[init]\n\tdefaultBranch = main\n' >"$GIT_CONFIG_GLOBAL"
A="$SANDBOX/home/alice"; B="$SANDBOX/home/bob"; REMOTE="$SANDBOX/remote"
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; exit 1; }
on() { local h="$1"; shift; HOME="$h" $BONDI "$@"; }
# A checkout with a GitHub origin, plus the project folder Claude makes on its first session there.
mkclone() {
  git init -q "$1/$2" && git -C "$1/$2" remote add origin git@github.com:acme/app.git
  mkdir -p "$1/.claude/projects/$(printf '%s' "$1/$2" | sed 's/[^A-Za-z0-9]/-/g')"
}

mkdir -p "$A/.claude/memory" "$A/.claude/skills/pr-review" "$B" "$REMOTE"
mkclone "$A" code/app
echo "- write short sentences" >"$A/.claude/memory/rules.md"
echo '{"model": "opus"}' >"$A/.claude/settings.json"
printf -- '---\nname: pr-review\ndescription: Review PRs.\n---\n' >"$A/.claude/skills/pr-review/SKILL.md"
enc="$(printf '%s' "$A/code/app" | sed 's/[^A-Za-z0-9]/-/g')"
mkdir -p "$A/.claude/projects/$enc/memory"
echo "- app fact" >"$A/.claude/projects/$enc/memory/MEMORY.md"
echo '{"oauthAccount": "decoy"}' >"$A/.claude/.claude.json"
echo '{}' >"$A/.claude/history.jsonl"
git init -q --bare -b main "$REMOTE/personal.git"
git init -q --bare -b main "$REMOTE/work.git"

cat >"$SANDBOX/personal.toml" <<'EOF'
name = "personal"
[claude]
skills = ["pr-review"]
files = ["settings.json"]
dirs = ["memory"]
EOF
cat >"$SANDBOX/work.toml" <<'EOF'
name = "work"
[[projects]]
github = "acme/app"
EOF

echo "== machine A creates two bondis"
on "$A" export --from "$SANDBOX/personal.toml" --remote "$REMOTE/personal.git" -y >/dev/null
on "$A" export --from "$SANDBOX/work.toml" --remote "$REMOTE/work.git" -y >/dev/null
files="$(git -C "$A/.config/bondi/profiles/personal" ls-files)"
echo "$files" | grep -q "claude.json\|jsonl" && fail "decoy committed"
pass "personal carries $(echo "$files" | wc -l | tr -d ' ') files, no login or transcript files"

echo "== machine B: a home machine installs personal, not work"
on "$B" add "$REMOTE/personal.git" -y >/dev/null
[ -f "$B/.claude/memory/rules.md" ] && pass "personal memory arrived on B"
[ ! -d "$B/.claude/projects" ] && pass "work memory is absent on B"

echo "== B also installs work; B keeps acme/app at a different path"
mkclone "$B" src/app
on "$B" add "$REMOTE/work.git" -y >/dev/null
encb="$(printf '%s' "$B/src/app" | sed 's/[^A-Za-z0-9]/-/g')"
[ -f "$B/.claude/projects/$encb/memory/MEMORY.md" ] && pass "work memory found B's checkout by its GitHub origin"

echo "== Claude on B learns something; A syncs"
echo "- learned on B" >"$B/.claude/memory/new.md"
on "$B" sync -y >/dev/null
on "$A" sync -y >/dev/null
grep -q "learned on B" "$A/.claude/memory/new.md" && pass "new memory file travelled B -> A"

echo "== one machine with two Claude folders"
mkdir -p "$B/.claude-second"; echo '{}' >"$B/.claude-second/settings.json"
if on "$B" add "$REMOTE/personal.git" -y >/dev/null 2>&1; then fail "installed without choosing a folder"; fi
pass "install refuses to guess between ~/.claude and ~/.claude-second"
on "$B" add "$REMOTE/personal.git" --claude-dir "~/.claude-second" -y >/dev/null
[ -f "$B/.claude-second/memory/rules.md" ] && pass "personal installed into ~/.claude-second when asked"
on "$B"
echo "ALL SANDBOX CHECKS PASSED"
