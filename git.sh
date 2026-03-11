#!/usr/bin/env bash
set -e

VERSION="${1:-v0.7.3}"
MESSAGE="${2:-validator dashboard ${VERSION}}"

git add .

if ! git diff --cached --quiet; then
  git commit -m "$MESSAGE"
else
  echo "Nothing staged for commit"
fi

git pull --rebase origin main
git push origin main

if ! git rev-parse "$VERSION" >/dev/null 2>&1; then
  git tag "$VERSION"
fi

git push origin "$VERSION"
