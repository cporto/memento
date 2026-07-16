#!/bin/bash
# wiki-lint.sh — Check wiki health after extraction pipeline runs
# Piggybacked onto wiki-extract-pipeline.sh as a post-check.
# Reports: duplicate slugs, broken [[links]], orphan pages.
# No changes to wiki content — read-only analysis appended to log.md.

set -euo pipefail

WIKI="$HOME/wiki"
LOG="$WIKI/log.md"
TODAY=$(date +%Y-%m-%d)
ISSUES=0

# --- Helper: find slug for a [[link]] name ---
slugify() {
    echo "$1" \
        | tr '[:upper:]' '[:lower:]' \
        | sed 's/ /-/g; s/[^a-z0-9-]//g; s/--*/-/g; s/^-//; s/-$//'
}

# --- 1. Duplicate slugs ---
DUPES=$(find "$WIKI/entities" "$WIKI/concepts" "$WIKI/decisions" "$WIKI/comparisons" "$WIKI/questions" \
    -name '*.md' 2>/dev/null \
    | sed 's|.*/||' | sort | uniq -d)

if [ -n "$DUPES" ]; then
    for slug in $DUPES; do
        locs=$(find "$WIKI" -name "$slug" 2>/dev/null | sort | tr '\n' ' ')
        echo "[lint] DUPLICATE SLUG: $slug appears at: $locs" >> "$LOG"
        ISSUES=$((ISSUES + 1))
    done
fi

# --- 2. Broken [[links]] in index.md ---
BROKEN=0
BROKEN_REPORT=""
while IFS= read -r link; do
    slug=$(slugify "$link")
    # Try exact match first
    found=$(find "$WIKI" -name "$slug.md" 2>/dev/null | head -1)
    # Try matching the slug against the 'title' field in frontmatter
    if [ -z "$found" ]; then
        found=$(grep -rl "^title: .*$link\$" "$WIKI/entities" "$WIKI/concepts" "$WIKI/decisions" "$WIKI/comparisons" "$WIKI/questions" 2>/dev/null | head -1)
    fi
    # Try case-insensitive slug match
    if [ -z "$found" ]; then
        found=$(find "$WIKI" -name '*.md' -path '*/entities/*' -o -name '*.md' -path '*/concepts/*' -o -name '*.md' -path '*/decisions/*' -o -name '*.md' -path '*/comparisons/*' -o -name '*.md' -path '*/questions/*' 2>/dev/null | while read f; do
            title=$(grep -m1 '^title: ' "$f" 2>/dev/null | sed 's/^title: *//; s/^"//; s/"$//')
            if [ "$(slugify "$title")" = "$slug" ]; then
                echo "$f"
                break
            fi
        done)
    fi
    if [ -z "$found" ]; then
        BROKEN_REPORT="$BROKEN_REPORT  [[$link]] → $slug (no file)\n"
        BROKEN=$((BROKEN + 1))
    fi
done < <(grep -o '\[\[[^]]*\]\]' "$WIKI/index.md" | sed 's/\[\[//;s/\]\]//' | sort -u)

if [ "$BROKEN" -gt 0 ]; then
    echo "[lint] BROKEN LINKS ($BROKEN found):" >> "$LOG"
    echo -e "$BROKEN_REPORT" >> "$LOG"
    ISSUES=$((ISSUES + BROKEN))
fi

# --- 3. Orphan pages (not in index) ---
ORPHANS=$(find "$WIKI/entities" "$WIKI/concepts" "$WIKI/decisions" "$WIKI/comparisons" "$WIKI/questions" \
    -name '*.md' 2>/dev/null | while read f; do
    base=$(basename "$f" .md)
    found=0
    while IFS= read -r link; do
        ls=$(slugify "$link")
        if [ "$ls" = "$base" ]; then
            found=1
            break
        fi
    done < <(grep -o '\[\[[^]]*\]\]' "$WIKI/index.md" | sed 's/\[\[//;s/\]\]//')
    if [ "$found" -eq 0 ]; then
        # Get title from frontmatter
        title=$(grep -m1 '^title: ' "$f" 2>/dev/null | sed 's/^title: *//; s/^"//; s/"$//')
        echo "  ${f#$WIKI/} (title: $title)"
    fi
done)

if [ -n "$ORPHANS" ]; then
    echo "[lint] ORPHAN PAGES:" >> "$LOG"
    echo "$ORPHANS" >> "$LOG"
    ISSUES=$((ISSUES + 1))
fi

# --- Summary ---
if [ "$ISSUES" -gt 0 ]; then
    echo "[lint] $TODAY | $ISSUES issue(s) found — see above." >> "$LOG"
else
    echo "[lint] $TODAY | Clean — no issues found." >> "$LOG"
fi
