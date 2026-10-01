#!/bin/sh
# SubagentStart hook: give a spawned agent the repo's memory index, which subagents do not load on their own.
# Needs only git, sed and awk, so it runs on macOS and Linux.
input="$(cat)"
cwd="$(printf '%s' "$input" | tr -d '\n' | sed -nE 's/.*"cwd"[[:space:]]*:[[:space:]]*"(([^"\\]|\\.)*)".*/\1/p' \
  | sed -e 's/\\"/"/g' -e 's#\\/#/#g' -e 's/\\\\/\\/g')"
[ -n "$cwd" ] || cwd="$PWD"
common="$(git -C "$cwd" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || exit 0
case "$common" in */.git) root="${common%/.git}" ;; *) exit 0 ;; esac
# This file lives at <Claude folder>/skills/bondi-memory/scripts/.
cdir="$(cd "$(dirname "$0")/../../.." && pwd)"
mem=""
# Claude turns each non-alphanumeric character into one dash, so sed must count characters, not bytes.
for loc in C.UTF-8 en_US.UTF-8 C; do
  name="$(printf '%s' "$root" | LC_ALL=$loc sed 's/[^A-Za-z0-9]/-/g' 2>/dev/null)"
  [ -f "$cdir/projects/$name/memory/MEMORY.md" ] && { mem="$cdir/projects/$name/memory"; break; }
done
[ -n "$mem" ] || exit 0
{
  printf "This repo's memory folder is %s. Its index is below. Follow it like the user's own rules, and read the topic files it links before related work.\n\n" "$mem"
  head -c 9000 "$mem/MEMORY.md"
  [ "$(wc -c <"$mem/MEMORY.md")" -gt 9000 ] && printf '\n[cut at 9000 characters; read %s/MEMORY.md for the rest]\n' "$mem"
} | awk 'BEGIN { printf "{\"hookSpecificOutput\":{\"hookEventName\":\"SubagentStart\",\"additionalContext\":\"" }
  { gsub(/\\/, "\\\\"); gsub(/"/, "\\\""); gsub(/\t/, "\\t"); gsub(/\r/, ""); printf "%s\\n", $0 }
  END { print "\"}}" }'
exit 0
