#!/bin/bash
# deploy_dashboard.sh — push updated dashboard_data.json to gh-pages branch
# Called from morning_run.py after export_dashboard.py succeeds

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
DASHBOARD_JSON="$REPO_DIR/dashboard/dashboard_data.json"
WORKTREE_DIR="$REPO_DIR/.gh-pages-worktree"

if [ ! -f "$DASHBOARD_JSON" ]; then
  echo "[deploy] ERROR: dashboard_data.json not found at $DASHBOARD_JSON"
  exit 1
fi

echo "[deploy] Deploying updated dashboard_data.json to gh-pages..."

# Set up worktree if it doesn't exist yet
if [ ! -d "$WORKTREE_DIR" ]; then
  echo "[deploy] Creating gh-pages worktree..."
  git -C "$REPO_DIR" worktree add "$WORKTREE_DIR" gh-pages
fi

# Copy latest data into worktree
cp "$DASHBOARD_JSON" "$WORKTREE_DIR/dashboard_data.json"

# Commit and push if changed
cd "$WORKTREE_DIR"
git add dashboard_data.json

if git diff --cached --quiet; then
  echo "[deploy] No changes in dashboard_data.json, skipping push."
  exit 0
fi

TODAY=$(date +%Y-%m-%d)
git commit -q -m "data: update dashboard $TODAY"
git push origin gh-pages -q

echo "[deploy] ✓ Pushed to gh-pages — https://simoncharmms.github.io/roboadvisor/"
