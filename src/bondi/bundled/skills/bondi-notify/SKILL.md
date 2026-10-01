---
name: bondi-notify
description: Send the user a desktop notification (macOS Notification Center, or notify-send on Linux). Use when the user asks to be told when something finishes or changes ("notify me when...", "ping me when it's done", "let me know when the build passes"), or when a long task they started needs their input and they may not be watching the session.
---

# bondi-notify

Notify the user with the script in this skill's folder:

```bash
sh "<this skill's base directory>/scripts/notify.sh" "<title>" "<message>" [sound]
```

- **Title:** at most 6 words, naming the thing: "PR merged", "Tests passed", "Input needed".
- **Message:** one sentence with the fact and, when there is one, the next step: "acme/app 123 merged. Pull main to continue."
- **Sound** is optional: a macOS sound name such as `Glass` or `Submarine`. Use one only when the user asked for a sound.

## When to notify

- The user asked to be told about an outcome. Notify once, when it happens, success or failure.
- A long task needs the user's input to continue. Notify, then stop and wait.

Do not notify for routine progress, for every step, or when the user is clearly watching the session.

## Waiting for something before you notify

For "tell me when X happens", poll with a shell check instead of re-asking a model. If the bondi CLI is
available, prefer a watch, which polls in the background for free:

```bash
bondi _watches --list                      # templates, for example pr-merged
bondi _watches pr-merged acme/app 123      # notifies by itself when it ends
```

Otherwise, run a background loop that exits on the outcome, then notify.
