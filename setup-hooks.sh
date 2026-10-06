#!/bin/sh
# ============================================================================
#  One-time setup: activate the repo's tracked git hooks for THIS clone.
#
#  core.hooksPath is LOCAL git config -- it is NOT version-controlled, so it
#  must be set once per clone. This points git at .githooks/, enabling the
#  pre-commit hook that auto-bumps the userscript @version so Tampermonkey
#  (which updates from the local app serving userscript.user.js) always sees
#  a newer version after each change.
#
#  Run:  sh setup-hooks.sh   (or ./setup-hooks.sh)
# ============================================================================
cd "$(dirname "$0")" || exit 1
git config core.hooksPath .githooks || { echo "FAILED - is this a git repo?"; exit 1; }
chmod +x .githooks/* 2>/dev/null || true
echo "Hooks activated. core.hooksPath = $(git config core.hooksPath)"
