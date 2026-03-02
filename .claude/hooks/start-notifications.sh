#!/bin/bash

# Claude Code Notification Automator
# Run this in your Claude Code terminal to enable automatic notifications
# Usage: source ./.claude/hooks/start-notifications.sh

# Get the directory where this script is located
HOOKS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$HOOKS_DIR")")"

# Function to send notification
send_notification() {
    local title="$1"
    local message="$2"
    local sound="${3:-Funk}"
    
    osascript -e "display notification \"$message\" with title \"$title\""
    afplay "/System/Library/Sounds/${sound}.aiff" 2>/dev/null &
}

# Export functions so they're available in subshells
export -f send_notification

# Make all hook scripts executable
chmod +x "$HOOKS_DIR"/*.sh 2>/dev/null

# Start notification background monitor
(
    while true; do
        # This will be triggered by Claude Code events
        # For now, we'll just keep the process alive
        sleep 1
    done
) &

# Store the background process ID
NOTIF_PID=$!
export NOTIF_PID

# Send startup notification
send_notification "🚀 Claude Code" "Notifications enabled for this session" "Purr"

echo "✅ Notifications activated (PID: $NOTIF_PID)"
echo "📝 Messages and file changes will trigger notifications"
echo ""
echo "To disable notifications, run: kill $NOTIF_PID"
