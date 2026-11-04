#!/usr/bin/env bash
# plan-progress.sh [--counts] <plan_file>
#
# Prints a formatted progress header for the next uncompleted task.
# Reads progress-header-phased.template or progress-header-flat.template
# and substitutes placeholders — those template files are the single source
# of truth for the header format.
#
# Exit 0  — header printed, NEXT_TASK_* lines follow
# Exit 1  — all tasks complete ("All tasks complete." printed)
# Exit 2  — usage error or file not found
# Exit 3  — no recognized task section heading, OR a task section with no tasks
#           (a clear message is printed to stderr)
#
# --counts — prints exactly three lines PHASES=<n, empty when the task section
#            has no ### phases>, TASKS=<n>, COMPLETED=<n> and exits 0 whether
#            or not tasks remain (exit 2/3 as above). A ### heading with no
#            tasks under it is not a phase.

set -uo pipefail

counts_only=0
if [ "${1:-}" = "--counts" ]; then
    counts_only=1
    shift
fi

if [ $# -ne 1 ]; then
    echo "Usage: plan-progress.sh [--counts] <plan_file>" >&2
    exit 2
fi

plan_file="$1"
if [ ! -f "${plan_file}" ]; then
    echo "ERROR: plan file not found: ${plan_file}" >&2
    exit 2
fi

_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Parse plan → emit key=value lines, one per output variable
parsed=$(awk "$(cat "$_dir/task_section.awk")"'
BEGIN {
    completed=0; remaining=0
    has_section=0; has_phases=0
    cur_phase_num=""; cur_done=0; cur_rem=0; cur_subtask_num=""
    phase_count=0; ordinal=0
    comp_phases=0; rem_phases=0; breakdown=""
    found_next=0
    next_name=""; next_phase=""; next_num=0
}

found { has_section=1 }

found && /^### / {
    flush()
    has_phases=1
    h=substr($0,5)
    cur_phase_num=(match(h,/^[0-9]+/)) ? substr(h,RSTART,RLENGTH) : ""
    cur_done=0; cur_rem=0; cur_subtask_num=""
    next
}

found && /^#### / {
    h=substr($0,6)
    cur_subtask_num=""
    if (match(h,/[0-9]+\.[0-9]+/)) {
        task_id=substr(h,RSTART,RLENGTH)
        dot=index(task_id,".")
        cur_subtask_num=substr(task_id,dot+1)
    }
    next
}

found && /^- \[[xX]\]/ { completed++; cur_done++ }

found && /^- \[ \]/ {
    remaining++; cur_rem++
    if (!found_next) {
        found_next=1
        name=$0; sub(/^- \[ \] /,"",name)
        next_name=name
        if (has_phases) {
            next_phase=(cur_phase_num!="") ? cur_phase_num : (ordinal+1)
            next_num=(cur_subtask_num!="") ? (cur_subtask_num+0) : (cur_done+cur_rem)
        } else {
            next_num=completed+remaining
        }
    }
}

END {
    if (has_phases) flush()
    if (found_next && has_phases && next_phase=="") next_phase=0
    if (!has_section) { print "NO_SECTION=1"; exit }
    total=completed+remaining
    if (total==0) { print "EMPTY_SECTION=1"; exit }
    print "PHASES="       (has_phases ? phase_count : "")
    print "TASKS="        total
    print "COMPLETED="    completed
    if (remaining==0) { print "ALL_DONE=1"; exit }

    fill=int(completed/total*12)
    bar=""
    for(i=1;i<=12;i++) bar=bar (i<=fill ? "\342\226\210" : "\342\226\221")
    pct=int(completed/total*100+0.5)

    print "HAS_PHASES="   has_phases
    print "BAR="          bar
    print "PCT="          pct
    print "TOTAL="        total
    print "REMAINING="    remaining
    print "COMP_PHASES="  comp_phases
    print "REM_PHASES="   rem_phases
    print "BREAKDOWN="    breakdown
    print "NEXT_PHASE="   next_phase
    print "NEXT_NUM="     next_num
    # NEXT_NAME last — may contain spaces, parsed separately
    print "NEXT_NAME="    next_name
}

function flush(    total_ph, ph_num) {
    total_ph=cur_done+cur_rem
    if (total_ph==0) return
    ordinal++
    ph_num=(cur_phase_num!="") ? cur_phase_num : ordinal
    phase_count++
    if (cur_rem==0) {
        comp_phases++
    } else {
        rem_phases++
        breakdown=(breakdown=="") ? ("§"ph_num":"cur_rem) : (breakdown" §"ph_num":"cur_rem)
    }
}
' "${plan_file}")

# Check sentinel values before eval (task names may contain shell-special chars)
if echo "$parsed" | grep -q "^NO_SECTION=1"; then
    echo "WARNING: no recognized task section heading found." >&2
    exit 3
fi
if echo "$parsed" | grep -q "^EMPTY_SECTION=1"; then
    echo "No tasks found in the plan file (task section is empty)." >&2
    exit 3
fi
if [ "$counts_only" -eq 1 ]; then
    echo "$parsed" | grep -E "^(PHASES|TASKS|COMPLETED)="
    exit 0
fi
if echo "$parsed" | grep -q "^ALL_DONE=1"; then
    echo "All tasks complete."
    exit 1
fi

# Extract values that may contain shell-special chars before eval
NEXT_NAME=$(echo "$parsed" | grep "^NEXT_NAME=" | head -1 | cut -d= -f2-)
BREAKDOWN=$(echo "$parsed" | grep "^BREAKDOWN=" | head -1 | cut -d= -f2-)

# Eval only the safe numeric/simple lines
eval "$(echo "$parsed" | grep -Ev "^(NEXT_NAME|BREAKDOWN)=")"

# Pluralisation helpers
task_label()  { [ "$1" -eq 1 ] && echo "1 task"  || echo "$1 tasks";  }
phase_label() { [ "$1" -eq 1 ] && echo "1 phase" || echo "$1 phases"; }

TOTAL_PHASES=$((COMP_PHASES + REM_PHASES))
COMPLETED_LABEL=$(task_label "$COMPLETED")
REMAINING_LABEL=$(task_label "$REMAINING")
COMPLETED_PHASES_LABEL=$(phase_label "$COMP_PHASES")
REMAINING_PHASES_LABEL=$(phase_label "$REM_PHASES")

# Select template
# shellcheck disable=SC2153  # NEXT_NUM is assigned by the eval above
if [ "$HAS_PHASES" -eq 1 ]; then
    template="$_dir/progress-header-phased.template"
    PHASE="$NEXT_PHASE"
    TASK_NUM="$NEXT_NUM"
else
    template="$_dir/progress-header-flat.template"
    TASK_NUM="$NEXT_NUM"
fi

# Render: substitute placeholders using awk (safe with any chars in values)
awk -v BAR="$BAR" \
    -v PCT="$PCT" \
    -v COMPLETED="$COMPLETED" \
    -v TOTAL="$TOTAL" \
    -v PHASE="${PHASE:-}" \
    -v TASK_NUM="$TASK_NUM" \
    -v TASK_NAME="$NEXT_NAME" \
    -v COMPLETED_LABEL="$COMPLETED_LABEL" \
    -v COMPLETED_PHASES_LABEL="${COMPLETED_PHASES_LABEL:-}" \
    -v REMAINING_LABEL="$REMAINING_LABEL" \
    -v REMAINING_PHASES_LABEL="${REMAINING_PHASES_LABEL:-}" \
    -v COMP_PHASES="$COMP_PHASES" \
    -v TOTAL_PHASES="$TOTAL_PHASES" \
    -v PHASE_BREAKDOWN="${BREAKDOWN:-}" \
'
function sub_ph(line,    k, v) {
    gsub(/\{\{BAR\}\}/,                      BAR,                      line)
    gsub(/\{\{PCT\}\}/,                      PCT,                      line)
    gsub(/\{\{COMPLETED\}\}/,               COMPLETED,                 line)
    gsub(/\{\{TOTAL\}\}/,                   TOTAL,                     line)
    gsub(/\{\{PHASE\}\}/,                   PHASE,                     line)
    gsub(/\{\{TASK_NUM\}\}/,                TASK_NUM,                  line)
    gsub(/\{\{TASK_NAME\}\}/,               TASK_NAME,                 line)
    gsub(/\{\{COMPLETED_LABEL\}\}/,         COMPLETED_LABEL,           line)
    gsub(/\{\{COMPLETED_PHASES_LABEL\}\}/,  COMPLETED_PHASES_LABEL,    line)
    gsub(/\{\{REMAINING_LABEL\}\}/,         REMAINING_LABEL,           line)
    gsub(/\{\{REMAINING_PHASES_LABEL\}\}/,  REMAINING_PHASES_LABEL,    line)
    gsub(/\{\{COMP_PHASES\}\}/,             COMP_PHASES,               line)
    gsub(/\{\{TOTAL_PHASES\}\}/,            TOTAL_PHASES,              line)
    gsub(/\{\{PHASE_BREAKDOWN\}\}/,         PHASE_BREAKDOWN,           line)
    return line
}
{ print sub_ph($0) }
' "$template"

echo ""
echo "NEXT_TASK_NAME=${NEXT_NAME}"
echo "NEXT_TASK_PHASE=${NEXT_PHASE:-}"
echo "NEXT_TASK_NUMBER=${NEXT_NUM}"
