#!/usr/bin/env bash
# Publish shields.io endpoint badge JSON files to the `badges` branch.
#
# README badges read them from raw.githubusercontent.com, e.g.
# https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lukwam/gcs-bigquery-import/badges/coverage-badge.json
#
# Usage (in GitHub Actions, after actions/checkout): scripts/publish_badges.sh FILE...
set -euo pipefail

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

worktree=""
cleanup() {
    if [ -n "$worktree" ]; then
        git worktree remove --force "$worktree" 2>/dev/null || true
        worktree=""
    fi
}
trap cleanup EXIT

for attempt in 1 2 3; do
    worktree=$(mktemp -d)
    if git fetch --quiet --depth 1 origin badges 2>/dev/null; then
        git worktree add --quiet -B badges "$worktree" FETCH_HEAD
    else
        git worktree add --quiet --detach "$worktree"
        git -C "$worktree" checkout --quiet --orphan badges
        git -C "$worktree" rm -rf --quiet --ignore-unmatch .
    fi
    cp "$@" "$worktree/"
    git -C "$worktree" add -- "$(printf '%s\n' "$@" | xargs -n1 basename)"
    if git -C "$worktree" diff --cached --quiet; then
        echo "Badges unchanged"
        exit 0
    fi
    git -C "$worktree" commit --quiet -m "Update badges"
    if git -C "$worktree" push --quiet origin badges; then
        echo "Published $*"
        exit 0
    fi
    echo "Push failed (attempt $attempt), retrying"
    cleanup
    sleep $((attempt * 5))
done
exit 1
