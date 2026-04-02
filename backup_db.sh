#!/bin/bash
# Automated backup of Second Brain database
# Keeps last 7 daily backups + last 4 weekly backups

DIR="$(cd "$(dirname "$0")" && pwd)"
DB="$DIR/second_brain.db"
BACKUP_DIR="$DIR/backups"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB" ]; then
    echo "$(date): ERROR — database not found at $DB" >&2
    exit 1
fi

DATE=$(date +%Y-%m-%d)
DAY_OF_WEEK=$(date +%u)  # 1=Monday, 7=Sunday
BACKUP_FILE="$BACKUP_DIR/second_brain_${DATE}.db"

# Use sqlite3 .backup for a safe, consistent copy (no corruption risk)
sqlite3 "$DB" ".backup '$BACKUP_FILE'"
echo "$(date): Backed up to $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

# Keep weekly backups (Sundays) for 4 weeks, daily for 7 days
find "$BACKUP_DIR" -name "second_brain_*.db" -mtime +7 ! -name "*Sunday*" -delete 2>/dev/null

# Tag Sunday backups by renaming (if today is Sunday)
if [ "$DAY_OF_WEEK" -eq 7 ]; then
    cp "$BACKUP_FILE" "$BACKUP_DIR/second_brain_weekly_${DATE}.db"
fi

# Clean weekly backups older than 28 days
find "$BACKUP_DIR" -name "second_brain_weekly_*.db" -mtime +28 -delete 2>/dev/null

# Report
COUNT=$(ls -1 "$BACKUP_DIR"/second_brain_*.db 2>/dev/null | wc -l | tr -d ' ')
echo "$(date): $COUNT backup files in $BACKUP_DIR"
