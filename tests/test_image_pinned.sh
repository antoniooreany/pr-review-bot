#!/usr/bin/env bash
# T15 — assert all PR-Agent image references in the repo are pinned to a
# specific version (no `:latest`, no missing tag). Reproducibility guard.
set -euo pipefail

cd "$(dirname "$0")/.."

# Files that should reference the PR-Agent image.
CANDIDATES=(
    "docker-compose.yml"
    "templates/.gitlab-ci.yml.template"
)

FAIL=0
for file in "${CANDIDATES[@]}"; do
    if [ ! -f "$file" ]; then
        continue
    fi

    # Extract image references (image: pragent/pr-agent:...).
    # Matches: image: <name>:<tag>   (not just <name>)
    while IFS= read -r line; do
        # Strip whitespace and leading "image:" / "- image:"
        img=$(echo "$line" | sed -E 's/^[[:space:]]*-?[[:space:]]*image:[[:space:]]*//' | tr -d '"' | tr -d "'")
        if [ -z "$img" ]; then
            continue
        fi

        # Skip non-pragent images.
        if [[ "$img" != *pr-agent* ]]; then
            continue
        fi

        # Must have a tag (not just the image name).
        if [[ "$img" != *":"* ]]; then
            echo "FAIL: $file — image has no tag: $img"
            FAIL=1
            continue
        fi

        # Tag must not be :latest.
        tag="${img##*:}"
        if [ "$tag" = "latest" ]; then
            echo "FAIL: $file — image uses :latest (not pinned): $img"
            FAIL=1
            continue
        fi

        # Tag should look like a version (digits.dots, optionally with suffix).
        if ! echo "$tag" | grep -qE '^[0-9]+\.[0-9]+'; then
            echo "WARN: $file — image tag doesn't look like semver: $img"
        else
            echo "  $file — $img (pinned ✓)"
        fi
    done < <(grep -E 'image:' "$file" || true)
done

if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
echo "PASS: all PR-Agent image references are pinned"
exit 0
