"""Array lifecycle adapter, Python 3.9; reuses the existing owned services.

Called by Unraid started/stopping_svcs events. No watchdog or automatic retry.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path('/mnt/cache/appdata/plex-read-cache-trial')
SOURCE_MOUNT = Path('/mnt/zfs/zfs')


def command(argv, timeout=40):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[-1500:] or p.stdout[-1500:])
    return p.stdout


def owner(name, script):
    path = ROOT / name
    if not path.exists():
        return None
    d = json.loads(path.read_text())
    proc = Path('/proc') / str(d['pid'])
    try:
        fields = (proc/'stat').read_text().split()
        if fields[21] != d['start'] or fields[2] == 'Z':
            return None
        argv = (proc/'cmdline').read_bytes().split(b'\0')
    except FileNotFoundError:
        return None
    if str(ROOT/script).encode() not in argv:
        raise RuntimeError('Owner identity does not match expected service')
    return d


def ready():
    state = Path('/var/local/emhttp/var.ini').read_text()
    if 'mdState="STARTED"' not in state or 'fsState="Started"' not in state:
        return False
    return (os.path.ismount('/mnt/cache') and os.path.ismount(SOURCE_MOUNT)
            and (SOURCE_MOUNT/'Media/Films').is_dir()
            and (SOURCE_MOUNT/'Media/TV').is_dir()
            and subprocess.run(['pgrep', '-x', 'smbd'], stdout=subprocess.DEVNULL).returncode == 0)


def dashboard_start():
    existing = owner('dashboard-owner.json', 'dashboard_server.py')
    if not existing:
        with (ROOT/'dashboard.log').open('ab') as log:
            cfg = json.loads((ROOT/'config.json').read_text())
            child = subprocess.Popen([sys.executable, str(ROOT/'dashboard_server.py'), '--root', str(ROOT),
                                      '--bind', cfg.get('dashboard_bind', '127.0.0.1'),
                                      '--allow-network', cfg.get('dashboard_allow_network', '127.0.0.0/8')],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        for _ in range(50):
            if child.poll() is not None:
                raise RuntimeError('Dashboard startup failed; inspect dashboard.log')
            existing = owner('dashboard-owner.json', 'dashboard_server.py')
            if existing and existing['pid'] == child.pid:
                break
            time.sleep(.1)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(30):
        try:
            cfg = json.loads((ROOT/'config.json').read_text())
            with opener.open('http://'+cfg.get('dashboard_bind', '127.0.0.1')+':18779/api/status', timeout=1) as response:
                json.load(response)
            return existing
        except OSError:
            time.sleep(.1)
    raise RuntimeError('Dashboard readiness failed')


def start():
    if not ready():
        return {'result': 'deferred', 'reason': 'Array, SSD, media and Samba must be ready; original-media share retained'}
    dashboard = dashboard_start()
    existing = owner('owner.json', 'cache_service.py')
    command([sys.executable, str(ROOT/'cache_service.py'), 'start', '--config', str(ROOT/'config.json')])
    status = json.loads((ROOT/'status.json').read_text())
    if status.get('phase') != 'active' or time.time()-status.get('checked_at', 0) > 30:
        raise RuntimeError('Cache is not freshly active; no restart attempted')
    if not existing:
        # Reconnect only this share if a client attached to its direct-source boot fallback.
        command(['smbcontrol', 'smbd', 'close-share', 'PlexReadCache'], timeout=10)
    return {'result': 'already-running' if existing else 'started',
            'cache': owner('owner.json', 'cache_service.py'), 'dashboard': dashboard}


def stop():
    # Release dashboard files first; the existing supervisor restores direct-source SMB,
    # stops its own rclone child and unmounts FUSE before storage is taken away.
    d = owner('dashboard-owner.json', 'dashboard_server.py')
    if d:
        os.kill(d['pid'], signal.SIGTERM)
        for _ in range(100):
            if not owner('dashboard-owner.json', 'dashboard_server.py'):
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Owned dashboard did not stop')
    owner('owner.json', 'cache_service.py')  # Check command identity before the existing stop helper.
    command([sys.executable, str(ROOT/'cache_service.py'), 'stop', '--config', str(ROOT/'config.json')])
    return {'result': 'stopped', 'mount_present': os.path.ismount('/mnt/plex-read-cache')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('start', 'stop', 'check'))
    args = parser.parse_args()
    with open('/run/plex-read-cache-lifecycle.lock', 'a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'check':
            result = {'ready': ready(), 'cache': owner('owner.json', 'cache_service.py'),
                      'dashboard': owner('dashboard-owner.json', 'dashboard_server.py')}
        else:
            result = start() if args.action == 'start' else stop()
        print(json.dumps(result))


if __name__ == '__main__':
    main()
