#!/bin/bash

# Colors
BLUE='\033[0;34m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

echo "Starting log viewer for all running containers..."
echo "Press Ctrl+C to exit"

# Function to tail logs with prefix
tail_logs() {
    local container=$1
    local color_code=$2 # e.g., "34" for blue, "32" for green
    local label=$3
    
    # Check if container is running
    if docker ps | grep -q "$container"; then
        # Use while loop for better portability and buffering control
        docker logs -f -n 100 "$container" 2>&1 | while read -r line; do
            printf "\033[0;${color_code}m[${label}]\033[0m %s\n" "$line"
        done &
    else
        echo "Container $container is not running"
    fi
}

# Kill background jobs on Ctrl+C
trap 'kill $(jobs -p)' SIGINT SIGTERM

# Get all running containers
CONTAINERS=$(docker ps --format '{{.Names}}')

if [ -z "$CONTAINERS" ]; then
    echo "No running containers found."
    exit 0
fi

# Colors for rotation
COLORS=("34" "32" "33" "35" "36" "31" "94" "92" "93" "95" "96")
COLOR_COUNT=${#COLORS[@]}

i=0
for container in $CONTAINERS; do
    # Pick a color
    color_index=$((i % COLOR_COUNT))
    color_code=${COLORS[$color_index]}
    
    echo "Tailing logs for $container..."
    tail_logs "$container" "$color_code" "$container"
    
    ((i++))
done

# Wait for all background processes
wait
