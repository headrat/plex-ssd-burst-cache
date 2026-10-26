#!/bin/bash
set -eu
events=/usr/local/emhttp/plugins/plex-read-cache/event
mkdir -p "$events"
install -m 0755 /boot/config/plex-read-cache/unraid-start.sh "$events/started"
install -m 0755 /boot/config/plex-read-cache/unraid-stop.sh "$events/stopping_svcs"
# Install-only: safe before emhttp starts, and safe during ongoing playback.
