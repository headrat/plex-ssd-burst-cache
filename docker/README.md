# Docker with Unraid host integration

This packages the patched rclone engine, Python supervisor and dashboard in one
image. The Bash host helper owns mount propagation, Samba fallback and Unraid
events. It does not require Python on Unraid. Linux hosts only: Docker Desktop
cannot propagate this FUSE mount to a Windows host.

The host helper also supplies the dashboard's cache-delivery graph using Samba
and `ss` TCP counters. Blue measures acknowledged delivery from the dedicated
cache share to Plex, including RAM-cached data and protocol overhead. It is
separate from original-source reads and from Plex's transfer to the player.
Connections serving other data shares are excluded. Missing host telemetry is
shown as unavailable; it does not stop playback. Use the matching host helper
when updating an existing image.

See [actual validation and remaining limits](VALIDATION.md).

## Build

From the repository root on a Linux Docker host:

```sh
docker build -t plex-ssd-burst-cache:local .
```

The build pins both base images by digest, checks the upstream source archive
SHA-256 and runs the patched downloader tests before building the engine. Go
build concurrency is limited to two threads. No private deployment files enter
the build context. The runtime is root with `/dev/fuse` and `SYS_ADMIN`; these are
substantial privileges required for mounting. It has a read-only root filesystem,
no Docker socket, no host network, a private loopback control API, bounded logs,
two CPUs and a 1 GiB memory limit. The helper does not use `--privileged`.

This release supplies a build recipe; no public registry image is implied.
After building, use the resulting `sha256:...` image ID in configuration to pin
the image locally. Rebuilds of Debian package dependencies may change over time.

If using the separately supplied tested Linux amd64 image archive, import it
with `docker load -i plex-ssd-burst-cache-linux-amd64.tar.gz`, then set `image`
to the image ID recorded in [VALIDATION.md](VALIDATION.md). Importing does not
start the service. Verify the supplied archive checksum before importing.

## Configure a separate instance

Use the Unraid terminal as root. Copy `unraid-host.sh` and a reviewed copy of
`host-config.example.json` to `/boot/config/plex-burst/`; name the configuration
`config.json` and restrict it to root (`chmod 600`). Paths must be canonical,
under `/mnt`, without spaces, and must not overlap. Use an explicit SSD pool
path for `state`; it contains both cached data and bounded logs. A different
source requires a fresh state directory to prevent stale cache reuse.

Set `source` to the original media folder. Use a new dedicated empty `export`
folder and a new share/container name. **Do not reuse the running native trial's
share, mount or state directory.** The helper refuses an existing container with
different ownership and an existing unowned mount. Parent storage directories
must already exist on mounted storage.

Set `clients` to the Plex machine's IP plus localhost. This creates a read-only
guest share restricted by IP, suitable only for a trusted LAN. The default
dashboard listens on host loopback. For LAN access set both `dashboard_bind` to
Unraid's LAN address and `dashboard_network` to the trusted client CIDR. Do not
publish it to the Internet. Port 18780 avoids the native trial's 18779.
The helper also allows the current Docker bridge gateway as one explicit host
address because Docker translates host loopback connections to that address.

Generate and review the dedicated Samba share configuration:

```sh
bash /boot/config/plex-burst/unraid-host.sh share-config /boot/config/plex-burst/config.json > /boot/config/plex-burst/share.conf
```

Add this line **once** at the end of `/boot/config/smb-extra.conf`, preserving its
existing contents and first checking that the chosen share name is unused:

```ini
include = /boot/config/plex-burst/share.conf
```

Validate with `testparm -s`, then `smbcontrol all reload-config`. The generated
share always defaults to original media; its temporary `/run` include is only
created after the container, host mount and watcher are ready.

```sh
bash /boot/config/plex-burst/unraid-host.sh start /boot/config/plex-burst/config.json
bash /boot/config/plex-burst/unraid-host.sh status /boot/config/plex-burst/config.json
bash /boot/config/plex-burst/unraid-host.sh stop /boot/config/plex-burst/config.json
```

Start is idempotent for the same healthy owned instance. A failed instance must
be inspected and stopped before starting again; there is no automatic restart
loop. The watcher checks every two seconds. Unhealthy/stopped containers trigger
direct-source fallback and close connections only to this share. Clients may
need to reconnect; seamless recovery of existing file handles is not promised.
Normal stop switches Samba first, then stops the container and unmounts its
dedicated export. It preserves all original media and SSD cache data.

Container `/export/media` is visible at host `export/media`. Only the dedicated
export self-bind is made shared. Never make all of `/mnt` shared just for this
package. The dashboard reports cache state, not proof that host Samba fallback
has completed; verify the actual share path and a read after any failure.

## Array lifecycle

After manually testing the separate share:

```sh
bash /boot/config/plex-burst/unraid-host.sh install-hooks /boot/config/plex-burst/config.json
```

Add that exact line to `/boot/config/go` **before** its existing `emhttp` launch,
preserving all other startup commands. It installs `started`, `stopping_docker`
and `stopping_svcs` hooks. The early Docker stop hook restores direct media before
Docker stops; the later hook is idempotent. The container uses `restart=no`
because the host helper controls storage readiness and startup order.

Actual reboot/array-cycle acceptance must be performed in a suitable maintenance
window; installing hooks alone does not prove it. This integration targets
Unraid 7. It has not been validated against a future plugin/event system.

## Upgrade, rollback and removal

Stop this instance before changing its configuration or image. Keep the previous
image ID and config; after testing a new build, pin its image ID and start. To
roll back, stop, restore the previous reviewed config/image ID and start. Never
replace a live FUSE worker beneath open Plex handles.

For removal, stop, remove only this instance's `install-hooks` line from `go`,
its `/usr/local/emhttp/plugins/plex-burst-NAME` event directory, and the exact
share include line installed above. Validate/reload Samba. Keep the state and
original media until you deliberately decide what to retain. Do not delete
global Docker caches, other plugin hooks or unrelated Samba shares.

Inspect `docker logs NAME`, `state/host-watch.log`, `state/rclone.log` and
`state/status.json` after a failure. Do not force/lazy-unmount a busy export:
identify and close its readers, then retry normal stop. If an owner disappears
while its mounts survive, inspect those mounts before recovering; the helper
intentionally refuses to adopt them automatically.
