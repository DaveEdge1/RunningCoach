#!/usr/bin/env bash
# Pull new runs, rebuild the analysis and calendar, and publish to GitHub Pages.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull -q --rebase --autostash  # the daily GitHub Action also commits
python scripts/sync.py
python scripts/analyze.py > /dev/null
python scripts/build_site.py
git add -A
if git diff --cached --quiet; then
  echo "Nothing new to publish."
else
  git commit -q -m "Update runs and calendar ($(date +%F))"
  git push -q
  echo "Published."
fi
