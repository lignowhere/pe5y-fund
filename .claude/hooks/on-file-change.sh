#!/bin/bash

# Monitor file changes and send notifications
# This runs after Write/Edit tool operations

file_path="$1"
operation="${2:-modified}"  # "write" or "edit"

if [[ -z "$file_path" ]]; then
    exit 0
fi

# Extract filename from path
filename=$(basename "$file_path")

# Send notification
notification_message="📝 File $operation

$filename"

osascript -e "display notification \"$notification_message\" with title \"Claude Code Activity\""

# Play subtle sound
afplay "/System/Library/Sounds/Glass.aiff" 2>/dev/null &

exit 0
