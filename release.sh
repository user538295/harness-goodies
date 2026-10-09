#!/usr/bin/env bash
# release.sh — automated release script for claude_goodies
# Usage: bash release.sh [--dry-run]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_JSON="$REPO_ROOT/.claude-plugin/plugin.json"
OMP_MARKETPLACE_JSON="$REPO_ROOT/.omp-plugin/marketplace.json"
CURSOR_PLUGIN_JSON="$REPO_ROOT/.cursor-plugin/plugin.json"
OPENCODE_PACKAGE_JSON="$REPO_ROOT/package.json"
# Every file carrying the release version; a release bumps them together.
VERSION_FILES=("$PLUGIN_JSON" "$OMP_MARKETPLACE_JSON" "$CURSOR_PLUGIN_JSON" "$OPENCODE_PACKAGE_JSON")

# ── Pure functions (all testable without git or filesystem) ───────────────────

# next_version <MAJOR.MINOR.PATCH> <has_new_items: true|false> → version string
next_version() {
  local current="$1" new_items="$2"
  local major minor patch
  IFS='.' read -r major minor patch <<< "$current"
  if [[ "$new_items" == "true" ]]; then
    echo "$major.$((minor + 1)).0"
  else
    echo "$major.$minor.$((patch + 1))"
  fi
}

# diff_has_new_items <git-diff-name-status output> → 0 if new item found, 1 if not
# "New item" = a file added (status A) directly under agents/, skills/, commands/, scripts/
diff_has_new_items() {
  local diff_output="$1"
  echo "$diff_output" | grep -qE $'^A\t(agents|skills|commands|scripts)/' || return 1
}

# filter_valid_tags <newline-separated tag list> → first vX.Y.Z tag, or empty
filter_valid_tags() {
  echo "$1" | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true
}

# check_on_main <branch-name> → 0 if "main", 1 otherwise
check_on_main() {
  [[ "$1" == "main" ]]
}

# check_clean_tree <git-status-porcelain output> → 0 if clean, 1 if dirty
check_clean_tree() {
  [[ -z "$1" ]]
}

# version_in <json-file> → its "version" field
version_in() {
  grep '"version"' "$1" | sed 's/.*"version": *"\([^"]*\)".*/\1/'
}

# current_version → reads "version" field from PLUGIN_JSON
current_version() {
  version_in "$PLUGIN_JSON"
}

# write_version <version> → sets "version" in every VERSION_FILES entry
write_version() {
  local file
  for file in "${VERSION_FILES[@]}"; do
    sed -i '' "s/\"version\": \"[^\"]*\"/\"version\": \"$1\"/" "$file"
  done
}

# ── Git wrappers ──────────────────────────────────────────────────────────────

find_last_valid_tag() {
  local tags
  tags=$(git -C "$REPO_ROOT" tag --sort=-version:refname)
  filter_valid_tags "$tags"
}

assert_on_main() {
  local branch
  branch=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
  if ! check_on_main "$branch"; then
    echo "Error: must be on 'main' (currently on '$branch')" >&2
    return 1
  fi
}

assert_clean_tree() {
  local status
  status=$(git -C "$REPO_ROOT" status --porcelain)
  if ! check_clean_tree "$status"; then
    echo "Error: working tree is dirty — commit or stash changes first" >&2
    return 1
  fi
}

has_new_items() {
  local last_tag="$1"
  local diff_output
  diff_output=$(git -C "$REPO_ROOT" diff --name-status "${last_tag}..HEAD")
  diff_has_new_items "$diff_output"
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
  local dry_run=false yes=false
  for arg in "$@"; do
    case "$arg" in
      --dry-run) dry_run=true ;;
      -y|--yes) yes=true ;;
    esac
  done

  assert_on_main
  if [[ "$dry_run" == "true" ]]; then
    assert_clean_tree 2>/dev/null || echo "Warning: working tree is dirty (ignored in dry-run)" >&2
  else
    assert_clean_tree
  fi

  local last_tag
  last_tag=$(find_last_valid_tag)
  if [[ -z "$last_tag" ]]; then
    echo "Error: no valid vX.Y.Z tags found — tag the initial release manually" >&2
    exit 1
  fi

  local current
  current=$(current_version)

  local new_items="false"
  if has_new_items "$last_tag" 2>/dev/null; then
    new_items="true"
  fi

  local next
  next=$(next_version "$current" "$new_items")
  local tag="v$next"
  local commit_msg="chore(release): bump version to $next"

  printf "Current version : %s\n" "$current"
  printf "Last tag        : %s\n" "$last_tag"
  printf "New items added : %s\n" "$new_items"
  printf "Next version    : %s\n" "$next"
  echo ""

  if [[ "$dry_run" == "true" ]]; then
    echo "[dry-run] Would execute:"
    printf "  1. sed version in %s files: %s → %s\n" "${#VERSION_FILES[@]}" "$current" "$next"
    printf "  2. git add %s\n" "${VERSION_FILES[*]#"$REPO_ROOT"/}"
    printf "  3. git commit -m \"%s\"\n" "$commit_msg"
    printf "  4. git tag -a %s -m \"Release %s\"\n" "$tag" "$tag"
    printf "  5. git push origin main\n"
    printf "  6. git push origin %s\n" "$tag"
    exit 0
  fi

  if [[ "$yes" == "false" ]]; then
    read -r -p "Have you updated sync-manifest.txt for any new files? [y/N] " confirm
    if [[ ! "${confirm:-}" =~ ^[yY]$ ]]; then
      echo "Aborted. Update sync-manifest.txt first." >&2
      exit 1
    fi
  fi

  write_version "$next"
  printf "Updated %s version files: %s → %s\n" "${#VERSION_FILES[@]}" "$current" "$next"

  git -C "$REPO_ROOT" add "${VERSION_FILES[@]}"
  git -C "$REPO_ROOT" commit -m "$commit_msg"
  echo "Committed."

  git -C "$REPO_ROOT" tag -a "$tag" -m "Release $tag"
  printf "Tagged: %s\n" "$tag"

  git -C "$REPO_ROOT" push origin main
  git -C "$REPO_ROOT" push origin "$tag"
  echo "Pushed."

  printf "\nRelease %s complete.\n\n" "$tag"
  echo "Post-release verification:"
  echo "  claude plugin update claude-goodies@user538295"
  echo "  omp plugin marketplace update user538295 && omp plugin upgrade omp-goodies@user538295"
  echo "    (first time on this machine: omp plugin marketplace add user538295/claude_goodies && omp plugin install omp-goodies@user538295)"
  echo "  opencode plugin update"
  echo "    (first time on this machine: opencode plugin add github:user538295/claude_goodies)"
  echo "  Cursor: Dashboard -> Plugins & MCPs -> Refresh the team marketplace (skip when Auto Refresh is on);"
  echo "    Cursor Marketplace updates appear after Cursor reviews them"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
