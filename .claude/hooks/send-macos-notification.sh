#!/bin/bash

# macOS Notification Hook using afplay
# Usage: ./send-macos-notification.sh 'Your message here' [sound]
# Sounds: Funk, Purr, Bell, Beep, Pop, Submarine, Glass, etc.

message="$1"
sound="${2:-Funk}"  # Default sound is Funk

if [[ -z "$message" ]]; then
    echo "⚠️  Usage: ./send-macos-notification.sh 'Your message here' [sound]"
    exit 1
fi

# Use osascript to send native macOS notification
osascript -e "display notification \"$message\" with title \"Claude Code\""

# Play notification sound using afplay
sound_file="/System/Library/Sounds/${sound}.aiff"
if [[ -f "$sound_file" ]]; then
    afplay "$sound_file" &
else
    echo "⚠️  Sound file not found: $sound_file"
    echo "Available sounds: Funk, Purr, Bell, Beep, Pop, Submarine, Glass, Tink"
fi
2
exit 0
