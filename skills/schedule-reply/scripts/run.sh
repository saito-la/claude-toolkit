#!/bin/bash
# launchd/cron entry point for the scheduler poller.
# launchd runs with a minimal env, so the only job here is to fix PATH and exec node.
# Output goes to stdout/stderr; launchd captures it via StandardOutPath/StandardErrorPath
# (see schedule-reply.plist.example). The poller writes its own JSONL log next to config.json,
# so this wrapper deliberately keeps no log path of its own.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') run start ====="
exec node "$DIR/poll.mjs" "$@"
