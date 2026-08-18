#!/bin/bash

# Smart Closet Streaming Autostart Script
# This script handles proper startup of the camera streaming backend

set -e

PROJECT_DIR="/home/kdt/smart_closet_backend"
VENV_DIR="$PROJECT_DIR/.venv"
LOG_DIR="/home/kdt/smart-closet-logs"
LOG_FILE="$LOG_DIR/streaming.log"

# Ensure log directory exists and is writable
mkdir -p "$LOG_DIR"
touch "$LOG_FILE" 2>/dev/null || true

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# Change to project directory
cd "$PROJECT_DIR"

# Log startup
echo "Starting Smart Closet Backend at $(date)" >> "$LOG_FILE" 2>/dev/null || true

# Start the application with output to log file
exec python3 app.py >> "$LOG_FILE" 2>&1
