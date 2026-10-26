"""Own both container children; host integration owns Samba and restart policy."""
import json
import ipaddress
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


def configuration():
    def number(name, default, low, high):
        value = int(os.environ.get(name, default))
        if not low <= value <= high:
            raise ValueError(name + ' outside supported range')
        return value
    cfg = dict(mode='container', root='/state', source='/media', mount='/export/media',
               rclone='/usr/local/bin/rclone-burst1', rc_port=5579,
               rclone_expected_version='rclone v1.75.2-burst1', sample_seconds=1,
               read_ahead_mib=number('READ_AHEAD_MIB', 999, 1, 16384),
               read_ahead_low_water_mib=number('LOW_WATER_MIB', 250, 0, 16383),
               max_cache_gib=number('MAX_CACHE_GIB', 100, 1, 1000),
               min_free_gib=number('MIN_FREE_GIB', 100, 1, 1000))
    if cfg['read_ahead_low_water_mib'] >= cfg['read_ahead_mib']:
        raise ValueError('Low water must be below read ahead')
    return cfg


def main():
    cfg = configuration()
    if not os.path.ismount('/media') or not os.path.ismount('/state') or not os.path.ismount('/export'):
        raise RuntimeError('Explicit media, SSD state and shared export mounts required')
    Path('/state/config.json').write_text(json.dumps(cfg))
    shutil.copyfile('/app/dashboard.html', '/state/dashboard.html')
    children = []
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    result = 0
    try:
        children.append(subprocess.Popen([sys.executable, '/app/cache_service.py', 'supervise', '--config', '/state/config.json']))
        dashboard = [sys.executable, '/app/dashboard_server.py', '--root', '/state', '--bind', '0.0.0.0',
                     '--allow-network', os.environ.get('DASHBOARD_ALLOW_NETWORK', '127.0.0.0/8')]
        if os.environ.get('DASHBOARD_HOST_ADDRESS'):
            address = ipaddress.ip_address(os.environ['DASHBOARD_HOST_ADDRESS'])
            dashboard += ['--allow-network', str(address) + ('/32' if address.version == 4 else '/128')]
        children.append(subprocess.Popen(dashboard))
        while not stopping:
            if any(child.poll() is not None for child in children):
                result = 1
                break
            time.sleep(.2)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    return result


if __name__ == '__main__':
    sys.exit(main())
