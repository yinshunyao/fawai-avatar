#!/usr/bin/env bash
# Upload dist/release archives to a GitHub Release for this repo.
# Requires: gh auth login; git tag pushed (or --draft).
#
# Usage:
#   bash packaging/publish_github_release.sh
#   TAG=v0.1.2 bash packaging/publish_github_release.sh
#   bash packaging/publish_github_release.sh --draft
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TAG="${TAG:-}"
if [[ -z "$TAG" ]]; then
  TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
fi
if [[ -z "$TAG" ]]; then
  VER="$(python3 -c "import pathlib,re; t=pathlib.Path('pyproject.toml').read_text(); print(re.search(r'version\s*=\s*\"([^\"]+)\"', t).group(1))")"
  TAG="v${VER}"
fi

DRAFT=0
NOTES_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --draft) DRAFT=1; shift ;;
    --notes) NOTES_FILE="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

DIST="${DIST_DIR:-$ROOT/dist/release}"
shopt -s nullglob
FILES=( "$DIST"/fawai-avatar-*.tar.gz "$DIST"/fawai-avatar-*.zip "$DIST"/fawai-avatar-*.sha256 )
if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "No archives under $DIST — run packaging/build_release.py first (or CI)." >&2
  exit 1
fi

echo "Publishing ${#FILES[@]} asset(s) to GitHub Release $TAG"
ARGS=( release create "$TAG" "${FILES[@]}" --title "$TAG" --verify-tag )
if [[ "$DRAFT" == "1" ]]; then
  ARGS=( release create "$TAG" "${FILES[@]}" --title "$TAG" --draft )
fi
if [[ -n "$NOTES_FILE" ]]; then
  ARGS+=( --notes-file "$NOTES_FILE" )
else
  ARGS+=( --generate-notes )
fi

# If release already exists, upload assets instead
if gh release view "$TAG" >/dev/null 2>&1; then
  echo "Release $TAG exists — uploading assets"
  gh release upload "$TAG" "${FILES[@]}" --clobber
else
  gh "${ARGS[@]}"
fi

echo "Done: https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/tag/$TAG"
