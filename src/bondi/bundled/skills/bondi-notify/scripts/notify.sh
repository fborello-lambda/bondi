#!/bin/sh
# Show a desktop notification: notify.sh "<title>" "<message>" [sound]
# macOS uses osascript, Linux uses notify-send. Without either, it does nothing: notifications are optional.
title="${1:-Claude}"
message="${2:-Done}"
sound="${3:-}"

if [ "$(uname)" = "Darwin" ] && command -v osascript >/dev/null 2>&1; then
  # Pass the text as arguments so quotes in it can never break the script.
  osascript - "$title" "$message" "$sound" <<'APPLESCRIPT'
on run argv
  if item 3 of argv is "" then
    display notification (item 2 of argv) with title (item 1 of argv)
  else
    display notification (item 2 of argv) with title (item 1 of argv) sound name (item 3 of argv)
  end if
end run
APPLESCRIPT
elif command -v notify-send >/dev/null 2>&1; then
  notify-send "$title" "$message"
fi
exit 0
