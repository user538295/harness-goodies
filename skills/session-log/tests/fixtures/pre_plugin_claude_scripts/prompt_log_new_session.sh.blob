#!/bin/bash
set -euo pipefail
# SessionStart hooks: non-zero exit is non-blocking (unlike UserPromptSubmit exit 2 which erases prompt), so no trap needed.

# Migrate legacy hooks: old /session-log on wrote hook paths into settings.json.
# Detect them, preserve the "logging was on" intent via flag file, create stubs
# so in-memory hooks don't error this session, then clean settings.json.
_SETTINGS="$HOME/.claude/settings.json"
if [ -f "$_SETTINGS" ] && grep -q "prompt_log" "$_SETTINGS" 2>/dev/null; then
    mkdir -p "$HOME/.claude/prompt-logs" "$HOME/.claude/scripts"
    touch "$HOME/.claude/prompt-logs/.enabled"
    for _s in prompt_log_save.sh prompt_log_new_session.sh; do
        printf '#!/bin/bash\n[ -f "%s/.claude/prompt-logs/.enabled" ] || exit 0\n' "$HOME" \
            > "$HOME/.claude/scripts/$_s"
        chmod +x "$HOME/.claude/scripts/$_s"
    done
    CLAUDE_SETTINGS="$_SETTINGS" python3 -c "
import json, os
p = os.environ['CLAUDE_SETTINGS']
with open(p) as f: s = json.load(f)
def clean(hd):
    r = {}
    for ev, entries in hd.items():
        c = [{**e, 'hooks': [h for h in e.get('hooks',[]) if 'prompt_log' not in h.get('command','')]} for e in entries]
        c = [e for e in c if e.get('hooks')]
        if c: r[ev] = c
    return r
if 'hooks' in s:
    s['hooks'] = clean(s['hooks'])
    if not s['hooks']: del s['hooks']
with open(p, 'w') as f: json.dump(s, f, indent=2)
" 2>/dev/null || true
fi

[ -f "$HOME/.claude/prompt-logs/.enabled" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0
source "$(dirname "${BASH_SOURCE[0]}")/prompt_log_lib.sh"

input=$(cat)
session_id=$(echo "$input" | jq -r '.session_id // ""')
cwd=$(echo "$input" | jq -r '.cwd // ""')

[ -z "$session_id" ] && exit 0
[ -z "$cwd" ] && exit 0

create_session_file "$session_id" "$cwd"
