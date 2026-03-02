# macOS Notification Automation Guide

## Quick Start

When you start a Claude Code terminal session, activate notifications with:

```bash
source ./.claude/hooks/start-notifications.sh
```

You'll see a notification confirming that notifications are enabled.

## What Happens Automatically

Once activated, you'll get notifications for:

1. **File Changes** - Every time Claude creates or edits a file
2. **Session Completion** - When Claude finishes a major task
3. **Subagent Tasks** - When sub-tasks complete

## Manual Notifications

Send a notification anytime with:

```bash
./.claude/hooks/send-macos-notification.sh "Your message here" [sound]
```

### Available Sounds
- `Funk` (default)
- `Purr`
- `Bell`
- `Beep`
- `Pop`
- `Submarine`
- `Glass`
- `Tink`

Examples:
```bash
# With custom sound
./.claude/hooks/send-macos-notification.sh "✅ Tests passed" Glass

# Multi-line message
./.claude/hooks/send-macos-notification.sh "Build Complete

✅ All tests passing
✅ No linting errors"
```

## Disabling Notifications

To stop notifications during a session:

```bash
kill $NOTIF_PID
```

Or simply close the terminal.

## Files Included

- `start-notifications.sh` - Activate notifications in your terminal
- `send-macos-notification.sh` - Manual notification sender
- `macos_notify.sh` - Automated hook for session events
- `on-file-change.sh` - File change notifications
- `hooks.json` - Hook configuration
- `settings.json` - macOS audio settings

## How It Works

1. The automation system monitors Claude Code events
2. When an event triggers (file write, session end, etc.), a notification is sent
3. Each notification includes both visual and audio alerts
4. All notifications appear in macOS Notification Center

Enjoy your automated notifications! 🎉
