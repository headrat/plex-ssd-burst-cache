#!/bin/bash
set -eu
root=/mnt/cache/appdata/plex-read-cache-trial
if ! mountpoint -q /mnt/cache; then exit 0; fi
if ! /usr/bin/python3 "$root/unraid_lifecycle.py" stop; then
  logger -t plex-read-cache 'Array-stop cleanup failed; inspect owned service state before forcing unmount'
  exit 1
fi
