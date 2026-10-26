#!/bin/bash
# Root-owned configuration only. One supervisor per named instance; no restart loop.
set -euo pipefail
action=${1:?start|stop|status|watch|share-config|install-hooks}
config=$(readlink -f "${2:?absolute config.json path}")
self=$(readlink -f "$0")
die() { echo "$*" >&2; exit 1; }
[[ $EUID == 0 && -f $config ]] || die 'Root and a real configuration file are required'
[[ $(stat -c %u "$config") == 0 ]] || die 'Configuration must be owned by root'
[[ -z $(find "$config" -maxdepth 0 -perm /022 -print) ]] || die 'Configuration must not be group/world writable'
jq -e '(.name|test("^[a-z][a-z0-9-]{0,39}$")) and (.share|test("^[A-Za-z][A-Za-z0-9_-]{0,39}$")) and (.clients|test("^[0-9a-fA-F.:/ ]+$")) and (.dashboard_bind|test("^[0-9.]+$")) and (.dashboard_network|test("^[0-9./]+$")) and (.image|test("^[a-zA-Z0-9_./:@-]+$")) and (.dashboard_port>=1024 and .dashboard_port<=65535) and (.read_ahead_mib>=1 and .read_ahead_mib<=16384) and (.low_water_mib>=0 and .low_water_mib<.read_ahead_mib) and (.max_cache_gib>=1 and .max_cache_gib<=1000) and (.min_free_gib>=1 and .min_free_gib<=1000)' "$config" >/dev/null || die 'Invalid configuration'
name=$(jq -r .name "$config"); share=$(jq -r .share "$config")
source=$(jq -r .source "$config"); state=$(jq -r .state "$config"); export_dir=$(jq -r .export "$config")
for path in "$source" "$state" "$export_dir"; do
  [[ $path =~ ^/mnt/[a-zA-Z0-9_./-]+$ && $path != */../* && $path != */./* && $path != */ && $path != /mnt ]] || die 'Paths must be simple absolute /mnt paths without spaces or traversal'
  [[ $(readlink -m "$path") == "$path" ]] || die 'Paths must be canonical, without symlinks'
done
for a in "$source" "$state" "$export_dir"; do
  for b in "$source" "$state" "$export_dir"; do
    [[ $a == "$b" ]] && continue
    [[ $a != "$b/"* ]] || die 'Source, state and export must not overlap'
  done
done
[[ $source != "$state" && $source != "$export_dir" && $state != "$export_dir" ]] || die 'Paths must differ'
runtime=/run/plex-burst-$name
include=$runtime/path.conf
mkdir -p "$runtime"
chmod 700 "$runtime"
fingerprint=$(sha256sum "$config" | cut -d' ' -f1)
container_exists() { docker inspect "$name" >/dev/null 2>&1; }
owned_container() { [[ $(docker inspect -f '{{index .Config.Labels "org.plex-burst.owner"}}' "$name" 2>/dev/null) == "$config" ]]; }
fuse_mounted() { [[ $(findmnt --first-only -rn -M "$export_dir/media" -o FSTYPE) == fuse.rclone ]]; }
reload_share() {
  testparm -s >/dev/null 2>&1
  smbcontrol all reload-config
  smbcontrol smbd close-share "$share"
}
fallback() {
  if [[ -f $include ]]; then
    cmp -s "$include" <(printf '# plex-burst %s\npath = %s/media\n' "$name" "$export_dir") || die 'Unowned runtime include'
    rm -- "$include"
    reload_share
  fi
}
healthy() {
  owned_container && [[ $(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null) == healthy ]] && fuse_mounted
}
stop_resources() {
  fallback
  if container_exists; then
    owned_container || die 'Refusing an unrelated container'
    docker stop -t 30 "$name" >/dev/null
    docker logs --tail 200 "$name" > "$state/container-last.log" 2>&1 || true
    docker rm "$name" >/dev/null
  fi
  if fuse_mounted; then fusermount3 -u "$export_dir/media"; fi
  if [[ -f $runtime/export-owned ]] && mountpoint -q "$export_dir"; then
    [[ $(cat "$runtime/export-owned") == "$export_dir" ]] || die 'Export ownership mismatch'
    umount "$export_dir"
  fi
  rm -f "$runtime/export-owned"
}
stop_watcher() {
  if [[ -f $runtime/watch.pid ]]; then
    read -r watch_pid watch_start < "$runtime/watch.pid"
    if [[ $watch_pid =~ ^[0-9]+$ && -f /proc/$watch_pid/stat && $(awk '{print $22}' "/proc/$watch_pid/stat") == "$watch_start" ]]; then
      kill -TERM "$watch_pid"
      for ((j=0;j<40;j++)); do
        if flock -n "$runtime/watch.lock" true; then break; fi
        sleep .1
      done
      flock -n "$runtime/watch.lock" true || die 'Watcher did not stop'
    fi
    rm -f "$runtime/watch.pid"
  fi
}
delivery_snapshot() {
  local smb tcp active=false
  smb=$(smbstatus --json | jq '{tcons: (.tcons | with_entries(.value |= {service, server_id}))}') || return 1
  tcp=$(ss -Htinp state established '( sport = :445 )') || return 1
  if [[ -f $include ]] && fuse_mounted; then active=true; fi
  (umask 077; jq -n --argjson smb "$smb" --arg tcp "$tcp" --arg share "$share" \
    --argjson observed_at "$(date +%s.%N)" --argjson cache_active "$active" \
    '{observed_at:$observed_at,cache_active:$cache_active,share:$share,smb:$smb,tcp:$tcp}' \
    > "$state/host-delivery-input.json.tmp") || return 1
  mv "$state/host-delivery-input.json.tmp" "$state/host-delivery-input.json"
}
case "$action" in
share-config)
  printf '# plex-burst %s: persistent direct-source fallback\n[%s]\npath = %s\nread only = yes\nguest ok = yes\nforce user = nobody\nforce group = users\nhosts allow = %s\ninclude = %s\n' "$name" "$share" "$source" "$(jq -r .clients "$config")" "$include"
  ;;
status)
  if healthy; then echo healthy; else echo inactive; exit 1; fi
  ;;
start)
  exec 9>"$runtime/control.lock"; flock -n 9 || die 'Another lifecycle operation is running'
  # The share must already exist with its original-source fallback and owned include.
  [[ $(testparm -s --section-name="$share" --parameter-name=path 2>/dev/null) == "$source" || -f $include ]] || die 'Install the generated share-config first'
  testparm -s --section-name="$share" 2>/dev/null | grep -Fq "include = $include" || die 'Share runtime include missing'
  if container_exists; then
    owned_container || die 'Container name already belongs to another service'
    [[ $(docker inspect -f '{{index .Config.Labels "org.plex-burst.config"}}' "$name") == "$fingerprint" ]] || die 'Stop the previous configuration before changing it'
    healthy || die 'Existing container is not healthy; inspect then stop, never duplicate'
    flock -n "$runtime/watch.lock" true && die 'Healthy container has no host watcher; stop and start to recover'
    echo already-running; exit 0
  fi
  [[ -d $source ]] || die 'Source storage is absent'
  for path in "$source" "$(dirname "$state")" "$(dirname "$export_dir")"; do
    [[ -d $path && $(findmnt -T "$path" -rn -o TARGET) == /mnt/* ]] || die 'Persistent storage is not mounted'
  done
  mkdir -p "$state" "$export_dir/media"
  if [[ -f $state/.plex-burst-source ]]; then
    [[ $(cat "$state/.plex-burst-source") == "$source" ]] || die 'State belongs to another source; use a fresh state directory'
  else
    [[ -z $(find "$state" -mindepth 1 -print -quit) ]] || die 'Use an empty state directory for first setup'
    printf '%s\n' "$source" > "$state/.plex-burst-source"
  fi
  [[ -z $(find "$export_dir" -mindepth 1 -maxdepth 1 ! -name media -print -quit) ]] || die 'Export directory is not dedicated'
  mountpoint -q "$export_dir" && die 'Unowned export mount exists; inspect it before recovery'
  [[ -z $(find "$export_dir/media" -mindepth 1 -print -quit) ]] || die 'Export mountpoint is not empty'
  trap 'stop_watcher; stop_resources' EXIT
  mount --bind "$export_dir" "$export_dir"
  printf '%s\n' "$export_dir" > "$runtime/export-owned"
  mount --make-rshared "$export_dir"
  docker run -d --name "$name" --label "org.plex-burst.owner=$config" --label "org.plex-burst.config=$fingerprint" \
    --restart=no --read-only --device /dev/fuse --cap-add SYS_ADMIN \
    --pids-limit 128 --cpus 2 --memory 1g \
    --tmpfs /tmp:rw,nosuid,nodev,size=32m --tmpfs /run:rw,nosuid,nodev,size=8m \
    --mount "type=bind,src=$source,dst=/media,readonly" \
    --mount "type=bind,src=$state,dst=/state" \
    --mount "type=bind,src=$export_dir,dst=/export,bind-propagation=rshared" \
    -p "$(jq -r .dashboard_bind "$config"):$(jq -r .dashboard_port "$config"):18779" \
    -e "DASHBOARD_ALLOW_NETWORK=$(jq -r .dashboard_network "$config")" \
    -e "DASHBOARD_HOST_ADDRESS=$(docker network inspect bridge | jq -r '.[0].IPAM.Config[0].Gateway')" \
    -e "READ_AHEAD_MIB=$(jq -r .read_ahead_mib "$config")" -e "LOW_WATER_MIB=$(jq -r .low_water_mib "$config")" \
    -e "MAX_CACHE_GIB=$(jq -r .max_cache_gib "$config")" -e "MIN_FREE_GIB=$(jq -r .min_free_gib "$config")" \
    --log-opt max-size=10m --log-opt max-file=2 "$(jq -r .image "$config")" >/dev/null
  ready=false
  for ((i=0;i<45;i++)); do if healthy; then ready=true; break; fi; sleep 1; done
  if ! $ready; then
    findmnt -R "$export_dir" >&2 || true
    docker inspect -f '{{json .State}} {{json .Mounts}}' "$name" >&2 || true
    die 'Container readiness failed; inspect retained state/container-last.log'
  fi
  nohup bash "$self" watch "$config" >"$state/host-watch.log" 2>&1 9>&- </dev/null &
  for ((i=0;i<20;i++)); do if ! flock -n "$runtime/watch.lock" true; then break; fi; sleep .1; done
  flock -n "$runtime/watch.lock" true && die 'Host watcher did not start'
  printf '# plex-burst %s\npath = %s/media\n' "$name" "$export_dir" > "$include.tmp"
  mv "$include.tmp" "$include"
  reload_share
  trap - EXIT
  echo healthy
  ;;
watch)
  exec 8>"$runtime/watch.lock"; flock -n 8 || exit 0
  printf '%s %s\n' "$$" "$(awk '{print $22}' /proc/$$/stat)" > "$runtime/watch.pid"
  trap 'exit 0' TERM INT
  delivery_next=0
  while sleep 2; do
    if ! healthy; then
      exec 9>"$runtime/control.lock"; flock -n 9 || continue
      stop_resources
      echo 'Cache failed: share returned to original source; manual restart required'
      exit 1
    fi
    if (( $(date +%s) >= delivery_next )); then
      delivery_snapshot || true  # Optional telemetry must never interrupt playback.
      delivery_next=$(( $(date +%s) + 5 ))
    fi
  done
  ;;
stop)
  exec 9>"$runtime/control.lock"; flock 9
  stop_watcher
  stop_resources
  echo stopped
  ;;
install-hooks)
  [[ $self == /boot/config/plex-burst/* && $config == /boot/config/plex-burst/* ]] || die 'Place helper/config under /boot/config/plex-burst first'
  hookroot=/usr/local/emhttp/plugins/plex-burst-$name/event
  mkdir -p "$hookroot"
  for event in started stopping_docker stopping_svcs; do
    operation=stop; [[ $event == started ]] && operation=start
    printf '#!/bin/bash\n# plex-burst managed hook\nbash %q %s %q >>/var/log/plex-burst-%s.log 2>&1\n' "$self" "$operation" "$config" "$name" > "$hookroot/$event"
    chmod 755 "$hookroot/$event"
  done
  echo 'Hooks installed for this boot. Add install-hooks to /boot/config/go before emhttp for persistence.'
  ;;
*) die 'Unknown action';;
esac
