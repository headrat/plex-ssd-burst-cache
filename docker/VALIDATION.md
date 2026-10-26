# Docker acceptance — 26 October 2026

Tested on Unraid **7.3.4**, Docker **29.8.2**, Linux amd64 and an SSD Btrfs pool.
Image ID: `sha256:90e88c1a315c216d359e313a80e911e9849f2a6ef3498c66dc77d370530c8da7`.
The image contains the patched `rclone v1.75.2-burst1` engine and Python 3.12.

The test used a separate generated 160 MiB file, fresh cache state, dedicated
export mount, container, port and Samba share. The real native Plex cache stayed
running; its owner records and configuration were byte-identical before/after.

Passed:

- Engine downloader/common tests during the Docker build.
- Six existing native lifecycle unit checks and actual native dashboard HTTP.
- Container configuration bounds, including the unchanged 999/250 MiB defaults.
- Container FUSE propagation to the host and read-only enforcement.
- Advancing reads, multiple seeks and complete SHA-256 equality.
- Repeated start kept the same container; no duplicate supervisor was launched.
- Dashboard access through the host's published loopback port.
- Exact source-byte plateaus and subsequent refill bursts with zero cache errors.
- Actual SMB file reads through cache, then direct source after graceful stop.
- Actual SMB direct-source read after forcibly killing the test container.
- Removal of the test container, export mount and temporary share configuration.

The bounded Docker burst test used **64 MiB high / 16 MiB low** to exercise a
refill quickly. Six consecutive observations reported the same source-byte
counter before a refill. This proves the packaged behavior on that test, not a
new real-film 999 MiB acceptance run. The existing native two-film 999/250 MiB
results remain separate evidence.

Full 160 MiB source, host-cache, cached-SMB and both fallback-SMB reads matched:
`00a08c68c566d82caceb735306851b99ba5749aa6d305b9450c1681b03ae52d0`.

Discovered and corrected during testing: the base image already contained
`/media`; Unraid returned duplicate matching mount-table rows; host loopback
traffic arrived at the container from Docker's bridge gateway. Samba on the test
host listened on LAN addresses only, so SMB acceptance used its existing LAN
listener. No Samba listener or security setting was changed. Unraid reported
that swap accounting was unavailable; the container memory limit was applied
without a swap limit.

Not established: actual reboot/array stop-start, a live Plex migration to this
image, seamless recovery of existing open SMB handles, other architectures,
AppArmor-enabled hosts, Unraid 8, whole-film completion, prolonged space pressure
or physical disk sleep. The Bash hooks and install path are supplied; boot
acceptance still needs a maintenance window.

## Dashboard cache-delivery follow-up

The later 26 October dashboard update adds separate source-read and cache-share
delivery plots. This is included when building current source; the earlier
exported image ID above records the original package, before this dashboard
update. Delivery uses TCP acknowledged bytes attributed to Samba processes
serving only the dedicated cache share (plus IPC), including RAM and SMB framing.
It is not physical SSD I/O or Plex-to-player bandwidth. Missing counters and
connections shared with other data shares produce gaps.

Six delivery-counter tests passed, covering rates, mixed shares, reconnects,
counter resets, idle connections, multiple connections and reused host snapshots.
An actual 64 MiB direct CIFS read through the existing Plex container measured
67,115,640 acknowledged bytes with **zero new source bytes**. A further check
included concurrent real Plex traffic. The native dashboard, API and rendered
graph were verified; the cache owner was unchanged during dashboard-only reload.

Updated Docker build ID:
`sha256:fec3cddec7119fa549c7618a523531044a88cdca0ae468e15099488dfc192e05`.
A separate 160 MiB paced SMB transfer through its isolated host integration
produced positive delivery samples in the actual container dashboard history,
peaking at 10.27 MiB/s with zero cache errors. The test container, share, watcher
and export mount were cleaned up; the live native cache stayed running.
