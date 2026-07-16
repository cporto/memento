#!/bin/bash
# wiki-summary.sh — Generate a compact wiki snapshot for sharing with Claude
# Usage: wiki-summary.sh [output_path]
# Default output: ~/wiki/wiki-summary.md
# Run from ~/wiki/ directory

set -euo pipefail

WIKI_DIR="${HOME}/wiki"
OUTPUT="${1:-${WIKI_DIR}/wiki-summary.md}"

# Redirect all output to the file
exec > "$OUTPUT"

echo "# Wiki Summary"
echo ""
echo "> Auto-generated snapshot of the knowledge wiki."
echo "> Regenerate with: \`~/wiki/wiki-summary.sh\`"
echo "> Last generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

echo ""
echo ""
echo "## Index"
cat "${WIKI_DIR}/index.md"

echo ""
echo ""
echo "## Recent Activity"
head -30 "${WIKI_DIR}/log.md"

echo ""
echo ""
echo "## Entity Pages"

for f in "${WIKI_DIR}/entities/"*.md; do
  [ -f "$f" ] || continue
  name=$(basename "$f" .md)
  echo ""
  echo "### ${name}"
  # Extract body only (skip frontmatter)
  awk 'BEGIN{p=0} /^---$/{if(p==0){p=1;next}if(p==1){p=2;next}} p==2{print}' "$f"
done

echo ""
echo ""
echo "## Concept Pages"

for f in "${WIKI_DIR}/concepts/"*.md; do
  [ -f "$f" ] || continue
  name=$(basename "$f" .md)
  echo ""
  echo "### ${name}"
  awk 'BEGIN{p=0} /^---$/{if(p==0){p=1;next}if(p==1){p=2;next}} p==2{print}' "$f"
done

echo ""
echo "Wiki snapshot complete. $(wc -l < "$OUTPUT") lines written." >&2