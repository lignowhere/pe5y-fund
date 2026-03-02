#!/bin/bash

# macOS Automated Notification Hook
# Triggered on Claude Code session completion
# Reads hook data from stdin and sends system notification

# Read hook data from stdin
hook_data=$(cat)

# Parse JSON data (simplified parsing without jq dependency)
hook_type=$(echo "$hook_data" | grep -o '"hookType":"[^"]*"' | cut -d'"' -f4)
project_dir=$(echo "$hook_data" | grep -o '"projectDir":"[^"]*"' | cut -d'"' -f4)
session_id=$(echo "$hook_data" | grep -o '"sessionId":"[^"]*"' | cut -d'"' -f4)

# Extract project name from path
project_name=$(basename "$project_dir")

# Set defaults if not found
hook_type="${hook_type:-Stop}"
project_name="${project_name:-Claude Code}"
session_id="${session_id:-N/A}"

# Build notification message
notification_message="✅ Session Complete

Project: $project_name
Session: ${session_id:0:8}..."

# Determine sound based on hook type
if [[ "$hook_type" == "SubagentStop" ]]; then
    sound="Purr"
    title="🤖 Subagent Task Complete"
else
    sound="Funk"
    title="🎉 Claude Code Done"
fi

# Send macOS notification
osascript -e "display notification \"$notification_message\" with title \"$title\""

# Play sound in background
afplay "/System/Library/Sounds/${sound}.aiff" 2>/dev/null &

exit 0
