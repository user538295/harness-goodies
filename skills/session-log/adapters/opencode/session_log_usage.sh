#!/bin/bash
# universal-session-log: managed
# Native OpenCode usage report. The universal dispatcher adds only its harness label.
set -euo pipefail
export LC_NUMERIC=C
command -v jq >/dev/null 2>&1 || { echo "jq not found" >&2; exit 1; }
command -v sqlite3 >/dev/null 2>&1 || { echo "sqlite3 not found" >&2; exit 1; }

canonical_path() {
  local input="$1" current suffix="" parent
  case "$input" in
    /*) current="$input" ;;
    *) current="$PWD/$input" ;;
  esac
  while [ ! -e "$current" ] && [ "$current" != "/" ]; do
    suffix="/${current##*/}$suffix"
    parent="${current%/*}"
    [ -n "$parent" ] || parent="/"
    current="$parent"
  done
  if [ -f "$current" ]; then
    suffix="/${current##*/}$suffix"
    current="${current%/*}"
  fi
  if [ -e "$current" ]; then
    current="$(cd -P -- "$current" 2>/dev/null && pwd -P)" ||
      { echo "Cannot resolve OpenCode path: $1" >&2; return 1; }
  fi
  printf '%s%s\n' "$current" "$suffix"
}
path_components_safe() {
  local input="$1" current parent
  case "$input" in
    /*) current="$input" ;;
    *) current="$PWD/$input" ;;
  esac
  current="${current%/}"
  [ -n "$current" ] || current="/"
  while :; do
    [ ! -L "$current" ] || return 1
    [ "$current" = "/" ] && return 0
    parent="${current%/*}"
    [ -n "$parent" ] || parent="/"
    current="$parent"
  done
}
HOME_CANONICAL="$(canonical_path "${HOME:?HOME is required}")"
DEFAULT_CONFIG_HOME="$(canonical_path "$HOME_CANONICAL/.config")"
DEFAULT_DATA_HOME_RAW="$HOME_CANONICAL/.local/share"
DEFAULT_DATA_HOME="$(canonical_path "$DEFAULT_DATA_HOME_RAW")"
CONFIG_HOME="$(canonical_path "${XDG_CONFIG_HOME:-$DEFAULT_CONFIG_HOME}")"
DATA_HOME_RAW="${XDG_DATA_HOME:-$DEFAULT_DATA_HOME_RAW}"
DATA_HOME="$(canonical_path "$DATA_HOME_RAW")"
if ! path_components_safe "$DATA_HOME_RAW" ||
   ! path_components_safe "$DATA_HOME_RAW/opencode"; then
  echo "OpenCode database path is not safe: $DATA_HOME_RAW/opencode" >&2
  exit 1
fi
if [[ -n "${OPENCODE_CONFIG_DIR:-}" ||
      "$CONFIG_HOME" != "$DEFAULT_CONFIG_HOME" ||
      "$DATA_HOME" != "$DEFAULT_DATA_HOME" ]]; then
  echo "OpenCode root is relocated; universal session-log does not support custom roots" >&2
  exit 1
fi
DATA="$(canonical_path "$DATA_HOME_RAW/opencode")"
MSG_DIR="$DATA/storage/message"
discover_database_name() {
  local candidate latest="" candidate_time latest_time=-1
  for candidate in "$DATA"/opencode-*.db; do
    [ -f "$candidate" ] && [ ! -L "$candidate" ] || continue
    candidate_time="$(stat -f %m "$candidate" 2>/dev/null || stat -c %Y "$candidate" 2>/dev/null)" || continue
    [[ "$candidate_time" =~ ^[0-9]+$ ]] || continue
    if [ "$candidate_time" -gt "$latest_time" ]; then
      latest="$candidate"
      latest_time="$candidate_time"
    fi
  done
  [ -n "$latest" ] && basename "$latest"
}
database_name() {
  local channel="${OPENCODE_CHANNEL:-}"
  if [[ -n "$channel" ]]; then
    if [[ "$channel" == latest || "$channel" == beta || "$channel" == prod ||
          "${OPENCODE_DISABLE_CHANNEL_DB:-}" == 1 ||
          "${OPENCODE_DISABLE_CHANNEL_DB:-}" == true ]]; then
      printf '%s\n' "opencode.db"
    else
      channel="${channel//[^a-zA-Z0-9._ -]/-}"
      printf 'opencode-%s.db\n' "$channel"
    fi
  elif [ -f "$DATA/opencode.db" ]; then
    printf '%s\n' "opencode.db"
  else
    local discovered
    discovered="$(discover_database_name || true)"
    printf '%s\n' "${discovered:-opencode-local.db}"
  fi
}
if [[ -n "${OPENCODE_DB:-}" ]]; then
  [ "${OPENCODE_DB}" != ":memory:" ] || { echo "OpenCode in-memory databases are unsupported" >&2; exit 1; }
  case "$OPENCODE_DB" in
    /*) DB_RAW="$OPENCODE_DB" ;;
    *) DB_RAW="$DATA/$OPENCODE_DB" ;;
  esac
else
  DB_RAW="$DATA/$(database_name)"
fi
if [ -L "$DATA" ] || [ -L "$DB_RAW" ]; then
  echo "OpenCode database path is not safe: $DB_RAW" >&2
  exit 1
fi
DB="$(canonical_path "$DB_RAW")"
case "$DB" in
  "$DATA"/*) ;;
  *) echo "OpenCode database path is outside the configured data root: $DB" >&2; exit 1 ;;
esac
arg=""
check=0
for option in "$@"; do
  case "$option" in
    --check) check=1 ;;
    --latest)
      [ -z "$arg" ] || { echo "Only one session target is allowed" >&2; exit 2; }
      arg="--latest"
      ;;
    -h|--help) echo "usage: $(basename "$0") [session-id | --latest] [--check]"; exit 0 ;;
    -*) echo "Invalid session target" >&2; exit 2 ;;
    *)
      [ -z "$arg" ] || { echo "Only one session target is allowed" >&2; exit 2; }
      arg="$option"
      ;;
  esac
done
case "$arg" in
  --latest) ;;
  /*) [ -f "$arg" ] || { echo "No session found for: $arg" >&2; exit 1; } ;;
  ''|*[!A-Za-z0-9_-]*) echo "Invalid session target" >&2; exit 2 ;;
esac

q() {
  local result
  if ! result=$(sqlite3 -readonly "$DB" "$1" 2>/dev/null); then
    echo "OpenCode SQLite query failed: $1" >&2
    return 1
  fi
  printf '%s\n' "$result"
}
session_exists=$(q "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='session';") || exit 1
message_exists=$(q "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='message';") || exit 1
session_v2_exists=$(q "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='session_v2';") || exit 1
session_message_exists=$(q "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='session_message';") || exit 1
session_count=0
session_v2_count=0
session_message_count=0
message_count=0
[ "$session_exists" = "1" ] && session_count=$(q "SELECT count(*) FROM session;") || true
[ "$session_v2_exists" = "1" ] && session_v2_count=$(q "SELECT count(*) FROM session_v2;") || true
[ "$session_message_exists" = "1" ] && session_message_count=$(q "SELECT count(*) FROM session_message;") || true
[ "$message_exists" = "1" ] && message_count=$(q "SELECT count(*) FROM message;") || true
session_message_seq_exists=0
[ "$session_message_exists" = "1" ] && session_message_seq_exists=$(q "SELECT count(*) FROM pragma_table_info('session_message') WHERE name='seq';") || true
session_message_order=rowid
[ "$session_message_seq_exists" = "1" ] && session_message_order=seq
if [ "$session_exists" = "1" ] && [ "$session_v2_exists" = "1" ]; then
  SESSION_TABLE="(SELECT id, parent_id, time_created, time_updated, title FROM session UNION ALL SELECT id, parent_id, time_created, time_updated, title FROM session_v2 WHERE id NOT IN (SELECT id FROM session))"
elif [ "$session_exists" = "1" ]; then
  SESSION_TABLE=session
elif [ "$session_v2_exists" = "1" ]; then
  SESSION_TABLE=session_v2
else
  SESSION_TABLE=""
fi
if [ "$session_message_exists" = "1" ] && [ "$message_exists" = "1" ] &&
  [ "$session_message_count" -gt 0 ] && [ "$message_count" -gt 0 ]; then
  STORAGE_KIND=db_hybrid
elif [ "$session_message_exists" = "1" ] && [ "$session_message_count" -gt 0 ]; then
  STORAGE_KIND=db_new
elif [ "$message_exists" = "1" ] && [ "$session_exists" = "1" ]; then
  STORAGE_KIND=db_current
elif [ "$session_message_exists" = "1" ] && [ "$session_v2_exists" = "1" ]; then
  STORAGE_KIND=db_new
else
  echo "No OpenCode session storage found in: $DB" >&2
  exit 1
fi
parent_in() {
  q "SELECT parent_id FROM $SESSION_TABLE WHERE id='$2';" | sed -n '1p'
}
root_of() {
  local id="$1" seen="" parent="" exists
  while :; do
    case "$id" in ''|*[!A-Za-z0-9_-]*) return 1 ;; esac
    case " $seen " in *" $id "*) return 1 ;; esac
    seen="$seen $id"
    exists=$(q "SELECT count(*) FROM $SESSION_TABLE WHERE id='$id';") || return 1
    [ "$exists" = "1" ] || return 1
    parent=$(parent_in "$SESSION_TABLE" "$id") || return 1
    [ -n "$parent" ] || break
    case "$parent" in ''|*[!A-Za-z0-9_-]*) return 1 ;; esac
    id="$parent"
  done
  printf '%s\n' "$id"
}
# The session the user is typing in is the root with the newest user prompt; a
# sibling still streaming assistant output has a newer time_updated. Roots without
# a prompt rank last; time_updated order breaks ties. Disk storage is consulted
# only for sessions without database records, mirroring sum_session.
latest_root() {
  local prompt_sources=() db_sources=() rows id prompt records file created ranked="" index=0
  if [ "$message_exists" = "1" ]; then
    prompt_sources+=("SELECT CAST(json_extract(data, '$.time.created') AS INTEGER) AS t FROM message WHERE session_id=s.id AND json_valid(data) AND json_extract(data, '$.role')='user'")
    db_sources+=("(SELECT count(*) FROM message WHERE session_id=s.id AND json_valid(data))")
  fi
  if [ "$session_message_exists" = "1" ]; then
    prompt_sources+=("SELECT CAST(json_extract(data, '$.time.created') AS INTEGER) AS t FROM session_message WHERE session_id=s.id AND type='user' AND json_valid(data)")
    db_sources+=("(SELECT count(*) FROM session_message WHERE session_id=s.id AND json_valid(data))")
  fi
  local prompt_sql="SELECT max(t) FROM (${prompt_sources[0]}${prompt_sources[1]:+ UNION ALL ${prompt_sources[1]}})"
  local records_sql="${db_sources[0]}${db_sources[1]:+ + ${db_sources[1]}}"
  rows=$(q "SELECT s.id || '|' || ifnull(($prompt_sql), '') || '|' || ($records_sql) FROM $SESSION_TABLE AS s WHERE s.parent_id IS NULL ORDER BY s.time_updated DESC;") || return 1
  while IFS='|' read -r id prompt records; do
    case "$id" in ''|*[!A-Za-z0-9_-]*) continue ;; esac
    if [ -z "$prompt" ] && [ "$records" = "0" ] && [ ! -L "$DATA/storage" ] && [ ! -L "$MSG_DIR" ] &&
      [ -d "$MSG_DIR/$id" ] && [ ! -L "$MSG_DIR/$id" ]; then
      for file in "$MSG_DIR/$id"/*.json; do
        [ -f "$file" ] && [ ! -L "$file" ] || continue
        created=$(jq -r 'select(type == "object" and .role == "user") | .time.created? | numbers | floor' "$file" 2>/dev/null) || continue
        [[ "$created" =~ ^[0-9]+$ ]] || continue
        [ -n "$prompt" ] && [ "$prompt" -ge "$created" ] || prompt="$created"
      done
    fi
    [[ "$prompt" =~ ^[0-9]+$ ]] || prompt=-1
    ranked+="$prompt|$index|$id"$'\n'
    index=$((index + 1))
  done <<<"$rows"
  printf '%s' "$ranked" | sort -t '|' -k1,1nr -k2,2n | cut -d '|' -f3 | sed -n '1p'
}

if [ "$arg" = "--latest" ]; then
  root=$(latest_root) || exit 1
else
  root=$(root_of "$arg") || exit 1
fi
[ -n "$root" ] || { echo "No session found." >&2; exit 1; }
case "$root" in ''|*[!A-Za-z0-9_-]*) echo "Invalid session ID" >&2; exit 1 ;; esac
title=$(q "SELECT title FROM $SESSION_TABLE WHERE id='$root';") || exit 1
children_of() {
  local parent="$1" rows child
  rows=$(q "SELECT id FROM $SESSION_TABLE WHERE parent_id='$parent' ORDER BY time_created;") || return 1
  while IFS= read -r child; do
    case "$child" in ''|*[!A-Za-z0-9_-]*) continue ;; esac
    printf '%s\n' "$child"
  done <<<"$rows"
}
walk_seen=""
walk_children() {
  local parent="$1" child children
  case " $walk_seen " in *" $parent "*) return ;; esac
  walk_seen="$walk_seen $parent"
  children=$(children_of "$parent") || return 1
  while IFS= read -r child; do
    [ -n "$child" ] || continue
    case " $walk_seen " in *" $child "*) continue ;; esac
    printf '%s\n' "$child"
    walk_children "$child" || return 1
  done <<<"$children"
}
sum_session() {
  local sid="$1" reqs=0 agg="" src="" file record role db_records=0 legacy_records=0 disk_available=0 disk_file=""
  local old_user=0 new_user=0 old_agg="" new_agg=""
  if [ ! -L "$DATA/storage" ] && [ ! -L "$MSG_DIR" ] &&
    [ -d "$MSG_DIR/$sid" ] && [ ! -L "$MSG_DIR/$sid" ]; then
    disk_file="$(find -P "$MSG_DIR/$sid" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null)" || return 1
    [ -n "$disk_file" ] && disk_available=1
  fi
  if [ "$STORAGE_KIND" = db_current ]; then
    db_records=$(q "SELECT count(*) FROM message WHERE session_id='$sid' AND json_valid(data);") || return 1
    src=db_current
  elif [ "$STORAGE_KIND" = db_hybrid ]; then
    db_records=$(q "SELECT count(*) FROM session_message WHERE session_id='$sid' AND json_valid(data);") || return 1
    legacy_records=$(q "SELECT count(*) FROM message WHERE session_id='$sid' AND json_valid(data);") || return 1
    src=db_hybrid
  else
    db_records=$(q "SELECT count(*) FROM session_message WHERE session_id='$sid' AND json_valid(data);") || return 1
    src=db_new
    if [ "$db_records" = "0" ] && [ "$message_exists" = "1" ]; then
      legacy_records=$(q "SELECT count(*) FROM message WHERE session_id='$sid' AND json_valid(data);") || return 1
      if [ "$legacy_records" -gt 0 ]; then
        db_records="$legacy_records"
        src=db_current
      fi
    fi
  fi
  if [ "$db_records" = "0" ] && [ "$legacy_records" = "0" ] && [ "$disk_available" = "1" ]; then
    src=disk
  fi
  case "$src" in
    disk)
      for file in "$MSG_DIR/$sid"/*.json; do
        [ -f "$file" ] || continue
        [ ! -L "$file" ] || continue
        if ! record=$(jq -r 'select(type == "object") | [(.role // ""), .] | @tsv' "$file"); then
          continue
        fi
        [ -n "$record" ] || continue
        role="${record%%$'\t'*}"
        [ "$role" = "user" ] && reqs=$((reqs + 1))
        agg+="$record"$'\n'
      done
      ;;
    db_current)
      reqs=$(q "SELECT count(*) FROM message WHERE session_id='$sid' AND json_valid(data) AND json_extract(data, '$.role')='user';") || return 1
      agg=$(q "SELECT json_extract(data, '$.role') || char(9) || data FROM message WHERE session_id='$sid' AND json_valid(data) ORDER BY time_created;") || return 1
      ;;
    db_hybrid)
      new_user=$(q "SELECT count(*) FROM session_message WHERE session_id='$sid' AND type='user' AND json_valid(data);") || return 1
      old_user=$(q "SELECT count(*) FROM message WHERE session_id='$sid' AND json_valid(data) AND json_extract(data, '$.role')='user' AND NOT EXISTS (SELECT 1 FROM session_message WHERE session_message.id = message.id);") || return 1
      reqs=$((old_user + new_user))
      old_agg=$(q "SELECT json_extract(data, '$.role') || char(9) || data FROM message WHERE session_id='$sid' AND json_valid(data) AND NOT EXISTS (SELECT 1 FROM session_message WHERE session_message.id = message.id) ORDER BY time_created;") || return 1
      new_agg=$(q "SELECT type || char(9) || data FROM session_message WHERE session_id='$sid' AND json_valid(data) ORDER BY $session_message_order;") || return 1
      agg="${old_agg}"$'\n'"${new_agg}"
      ;;
    db_new)
      reqs=$(q "SELECT count(*) FROM session_message WHERE session_id='$sid' AND type='user' AND json_valid(data);") || return 1
      agg=$(q "SELECT type || char(9) || data FROM session_message WHERE session_id='$sid' AND json_valid(data) ORDER BY $session_message_order;") || return 1
      ;;
  esac
  if [ -z "$agg" ]; then
    printf '%s 0 0 0 0 0 0 0 0\n' "$reqs"
    return
  fi
  parsed=$(printf '%s' "$agg" | jq -R -s -r --argjson r "$reqs" '
      def num:
        (if type == "number" then .
         elif type == "string" then (tonumber? // 0)
         else 0
         end)
        | if type == "number" and isfinite and . >= 0 then floor else 0 end;
      def cost_num:
        (if type == "number" then .
         elif type == "string" then (tonumber? // 0)
         else 0
         end)
        | if type == "number" and isfinite and . >= 0 then . else 0 end;
      def time_num:
        if type == "number" then
          if isfinite and . >= 0 then floor else null end
        elif type == "string" then
          (tonumber? // null) as $value |
          if ($value | type) == "number" and ($value | isfinite) and $value >= 0 then ($value | floor) else null end
        else null
        end;
      def time_value($d; $field):
        (($d.time? // {}) | if type == "object" then .[$field] else null end | time_num);
      def stream_or_created($d):
        (($d.time? // {}) as $time |
          if ($time | type) == "object" then
            if $time.streamed != null then ($time.streamed | time_num)
            elif $time.created != null then ($time.created | time_num)
            else null
            end
          else null
          end);
      def has_time_value($d; $field):
        (time_value($d; $field) != null);
      def field($p): ((.tokens // {}) | getpath($p) // 0 | num);
      def native_total:
        if .tokens?.total != null then (.tokens.total | num)
        else field(["input"]) + field(["output"]) + field(["reasoning"]) + field(["cache","read"]) + field(["cache","write"])
        end;
      def user_wait_tool: (if type == "array" then map(select(.type == "tool" and (.name == "question" or .name == "confirm" or .name == "ask"))) else [] end | length) > 0;
      [split("\n")[] | select(length > 0) | split("\t") as $p | select(($p | length) == 2) | {t: $p[0], d: ($p[1] | fromjson?)} | select(.d != null and (.d | type) == "object")] | sort_by((time_value(.d; "created") // 0)) as $M |
      (reduce $M[] as $m (
        {ai: 0, anchor: null, last_e: null};
        if $m.t == "user" then
          (if .anchor != null and .last_e != null and .last_e >= .anchor then .ai += (.last_e - .anchor) else . end)
          | .anchor = (time_value($m.d; "created")) | .last_e = null
        elif $m.t == "assistant" then
          (($m.d.content // []) | user_wait_tool) as $uw |
          if $uw and (has_time_value($m.d; "streamed") or has_time_value($m.d; "created")) then
            (stream_or_created($m.d)) as $e |
            (if .anchor != null and $e != null and $e >= .anchor then .ai += ($e - .anchor) else . end)
            | .anchor = null | .last_e = null
          else
            (if .anchor == null then .anchor = (time_value($m.d; "created")) else . end)
            | (if has_time_value($m.d; "completed") then
                (time_value($m.d; "completed")) as $e |
                if $e != null and (.last_e == null or $e > .last_e) then .last_e = $e else . end
              else . end)
          end
        else . end
      )) as $acc |
      ($acc | if .anchor != null and .last_e != null and .last_e >= .anchor then .ai + (.last_e - .anchor) else .ai end) as $ms |
      ([$M[] | select(.t == "assistant") | .d]) as $a |
      "\($r) \($a | map(field(["input"])) | add // 0) \($a | map(field(["output"])) | add // 0) \($a | map(field(["reasoning"])) | add // 0) \($a | map(field(["cache","write"])) | add // 0) \($a | map(field(["cache","read"])) | add // 0) \($a | map(native_total) | add // 0) \($a | map((.cost // 0) | cost_num) | add // 0) \($ms)"
    ') || return 1
  printf '%s\n' "$parsed"
}
format_hms() {
  local seconds="$1"
  printf '%02d:%02d:%02d' "$((seconds / 3600))" "$(((seconds % 3600) / 60))" "$((seconds % 60))"
}

line() {
  local label="$1" reqs="$2" input="$3" output="$4" reasoning="$5"
  local cache_write="$6" cache_read="$7" native_total="$8" cost="$9" milliseconds="${10}"
  printf '%s: requests: %s, working time: %s\n' "$label" "$reqs" "$(format_hms "$((milliseconds / 1000))")"
  printf 'est. used token: input: %s, output: %s, reasoning: %s, cache_write: %s, cache_read: %s, total_tokens: %s, cost: $%.4f\n' \
    "$input" "$output" "$reasoning" "$cache_write" "$cache_read" "$native_total" "$cost"
}

echo "session: $root${title:+  — $title}"
read -r reqs in out rea cw cr total cost ms <<<"$(sum_session "$root")" || exit 1
line "main" "$reqs" "$in" "$out" "$rea" "$cw" "$cr" "$total" "$cost" "$ms"
t_ms=$ms
t_reqs=$reqs; t_in=$in; t_out=$out; t_rea=$rea; t_cw=$cw; t_cr=$cr; t_total=$total
t_cost=$cost; t_subs=0
children=$(walk_children "$root") || exit 1
while read -r c; do
  [ -n "$c" ] || continue
  read -r creqs cin cout crea ccw ccr ctotal ccost cms <<<"$(sum_session "$c")" || exit 1
  line "sub-agent $c" "$creqs" "$cin" "$cout" "$crea" "$ccw" "$ccr" "$ctotal" "$ccost" "$cms"
  t_subs=$((t_subs+1))
  t_in=$((t_in+cin)); t_out=$((t_out+cout)); t_rea=$((t_rea+crea))
  t_cw=$((t_cw+ccw)); t_cr=$((t_cr+ccr)); t_total=$((t_total+ctotal))
  t_cost=$(jq -n --argjson a "$t_cost" --argjson b "$ccost" '$a + $b') || exit 1
done <<<"$children"
printf '\nTOTAL: requests: %s, sub-agents: %s, working time: %s\n' \
  "$t_reqs" "$t_subs" "$(format_hms "$((t_ms / 1000))")"
printf 'est. used token: input: %s, output: %s, reasoning: %s, cache_write: %s, cache_read: %s, total_tokens: %s, cost: $%.4f\n' \
  "$t_in" "$t_out" "$t_rea" "$t_cw" "$t_cr" "$t_total" "$t_cost"
if [ "$check" -eq 1 ]; then
  printf 'check: native OpenCode usage is authoritative; native cost fields are preserved\n'
fi
