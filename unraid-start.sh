#!/bin/bash
set -eu
# Array-start is emitted after storage/network services start. At early boot,
# installing these hooks must not touch the unmounted SSD directory.
root=/mnt/cache/appdata/plex-read-cache-trial
if ! mountpoint -q /mnt/cache || ! mountpoint -q /mnt/zfs/zfs; then
  logger -t plex-read-cache 'Startup deferred: storage is not mounted; original-media fallback retained'
  exit 0
fi
if ! /usr/bin/python3 "$root/unraid_lifecycle.py" start; then
  logger -t plex-read-cache 'Startup failed; inspect owned service logs; no automatic retry'
  exit 1
fi
