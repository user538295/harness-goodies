---
description: Manage session prompt logging and usage totals (on / off / status / usage / repair)
---
<!-- universal-session-log: managed -->

Pass the exact invocation arguments to the shared package with the explicit `opencode` identity. Never infer identity from files, directories, processes, or environment variables. Set `arguments` below to the exact invocation arguments, or to `status` when none were supplied. Shell-quote the assignment as one data value; never evaluate invocation text as shell syntax.

```bash
session_log_root="$HOME/.config/opencode/skills/session-log"
arguments='<exact invocation arguments, or status>'
if [[ -f "$session_log_root/install.sh" ]]; then
  bash "$session_log_root/install.sh" --harness opencode --arguments "$arguments"
else
  printf 'session-log: complete package is missing at %s\n' "$session_log_root" >&2
  false
fi
```

`usage` passes OpenCode's native report through unchanged after the one-line harness label. `on` may report `on — restart required`; restart OpenCode before relying on logging.
