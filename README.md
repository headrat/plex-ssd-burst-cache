# Plex SSD Burst Cache

An experimental, read-only SSD read-ahead cache for Plex media on Unraid.
It fills ahead in a burst, then pauses source-file reads until the cached window
needs topping up. A small dashboard shows source reads, cached data and free space.

```text
HDD-backed media → patched rclone / FUSE → SSD cache → read-only SMB share → Plex
```

## What it does

- Fills up to **999 MiB ahead**, then refills below **250 MiB**.
- Maintains separate requested regions for concurrent readers; cached bytes can be shared.
- Uses a **100 GiB soft cache budget** and **100 GiB free-space target**.
- Retains unused cached data for up to 24 hours, subject to eviction pressure.
- Provides a read-only dashboard and Unraid array start/stop hooks.
- Keeps the persistent SMB share pointed at original media; a temporary runtime
  include selects the cache while the supervisor is active.

The buffer is a read-ahead target, not a cap on total cached data. Open files can
exceed soft limits. Source-file read counters do not measure physical HDD sectors
or prove that disks are asleep; the filesystem may serve data from RAM.

## Status and measured results

**Prototype, not a turnkey Unraid plugin.** A custom `v1.75.2-burst1` engine is
based on rclone 1.75.2. This patch is not an upstream rclone release.

On 24 October 2026, two real Plex films advanced through measured source-byte
quiet intervals of approximately **30 and 60 seconds**, with roughly **750 MiB**
refills between them and zero reported cache errors. Isolated two-reader FUSE
tests verified bytes/seeks and 19.1/14.1-second quiet intervals.

These are bounded observations, not a benchmark or a guarantee for other hosts.
Whole-film acceptance, capacity beyond two real streams, prolonged cache pressure,
seamless active-handle failover and hard-crash recovery remain unproved. Startup
hooks were tested idempotently without restarting playback; an actual reboot and
array-stop/start cycle were not exercised.

## Requirements and layout

Tested deployment: Unraid 7.3.3, Linux FUSE3, Python 3.9.2, Samba and a mirrored
SSD cache pool. The engine was built with Go 1.26.10, CGO disabled.

The current adapters deliberately enforce this deployment layout:

| Purpose | Path |
|---|---|
| Supervisor/config/cache | `/mnt/cache/appdata/plex-read-cache-trial` |
| Original media | `/mnt/zfs/zfs/Media` with `Films` and `TV` |
| FUSE mount | `/mnt/plex-read-cache` |
| Read-only share | `PlexReadCache` |
| Temporary share include | `/run/plex-read-cache-path.conf` |
| Dashboard | Port 18779, loopback by default |
| rclone control | Loopback only, port 5579 |

Review the guarded paths before adapting another storage layout. Ensure Python,
FUSE and Samba are installed persistently; do not assume an interactive install
survives Unraid's RAM filesystem. The public source uses loopback dashboard
defaults, a small publication change which has **not been deployed to the live
trial**. To use a trusted LAN, explicitly configure the host address and client CIDR.

## Build the engine

In a fresh checkout of the official rclone `v1.75.2` tag, apply the included patch
and run its tests. The patch includes the minimal `cmd/plexburst` entry point.
Keep a separate build directory; never patch a running binary in place.

```sh
git apply /path/to/plex-ssd-burst-cache/burst-engine/burst-engine.patch
GOMAXPROCS=2 CGO_ENABLED=0 go test -p 1 ./vfs/vfscache/downloaders ./vfs/vfscommon
GOMAXPROCS=2 CGO_ENABLED=0 go build -p 1 -trimpath \
  -ldflags '-s -w -X github.com/rclone/rclone/fs.Version=v1.75.2-burst1' \
  -o rclone-burst1 ./cmd/plexburst
./rclone-burst1 version
```

Original source archive SHA-256:
`68afd7f68c84bd978966feae2116339aa7bf454b39c573910c462e87c3774d5a`.
The retained trial binary hash is
`ee2a852e8c8e34b14b011453d020afe41bdc100d39d95531801291152012faae`;
build reproducibility across other toolchains is not claimed. No binaries ship here.

## Integrate carefully

1. Test on a separate library before redirecting Plex's media mount. Preserve
   its configuration, original media volume and recovery route.
2. Put the reviewed engine, Python scripts and dashboard HTML in the configured
   root. Copy `config.example.json` to `config.json` and review free-space limits.
3. Configure a **read-only** Samba share with its persistent path set to original
   media and `include = /run/plex-read-cache-path.conf`. Apply host restrictions
   appropriate to your network; never expose the dashboard or control API publicly.
4. Start `cache_service.py start --config /path/to/config.json`, then the dashboard
   with `dashboard_server.py --root /path/to/root`. Verify actual media bytes,
   session progress, source-read bursts and original-media fallback.
5. For reviewed array integration, place the three `unraid-*.sh` scripts in
   `/boot/config/plex-read-cache/` and the lifecycle adapter in the configured root.
   Add `bash /boot/config/plex-read-cache/unraid-install.sh` **before** the existing
   `emhttp` launch in `/boot/config/go`, preserving all other startup work.

The installer only registers `started` and `stopping_svcs` hooks. It does not
create a Samba share, alter Plex, install dependencies or start another watcher.
The lifecycle adapter starts only after array/storage/Samba readiness. Failures
are logged under `plex-read-cache`; inspect ownership before retrying.

## Stop and recover

`cache_service.py stop --config /path/to/config.json` restores the share's
original-media path, closes only this share's connections, stops its owned engine
and unmounts FUSE. Existing playback handles may require a client retry.
Disable future startup by removing only the owned go-file block and event hooks;
do not replace a newer go file with an old snapshot. Keep original media intact.

The lifecycle tests run on Linux:

```sh
python3 tests/test_unraid_lifecycle.py ./unraid_lifecycle.py
```

Private machine state, credentials, media, binary caches and operational receipts
are intentionally excluded by an allowlist `.gitignore`. See [NOTICE.md](NOTICE.md)
for attribution and [LICENSE](LICENSE) for the MIT license.
