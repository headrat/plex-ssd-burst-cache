"""Unraid read-only Plex cache trial: guarded rclone mount and local monitoring.

Python 3.9 compatible. SMB's persistent share points at the original Media folder;
only the temporary /run include selects the cache while this service is healthy.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

STOP = False


def atomic(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) if isinstance(data, dict) else data)
    os.replace(tmp, path)


def owned(pidfile):
    if not pidfile.exists():
        return None
    d = json.loads(pidfile.read_text())
    stat = Path('/proc') / str(d['pid']) / 'stat'
    if stat.exists():
        fields = stat.read_text().split()
        if fields[21] == d['start'] and fields[2] != 'Z':
            return d
    return None


def run(argv, timeout=15):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError('Command failed: ' + argv[0] + ': ' + p.stderr[-1000:])
    return p.stdout


def share_path(cfg, active):
    path = Path(cfg['runtime_include'])
    wanted = '# Owned by Plex read-ahead trial\npath = ' + cfg['mount'] + '\n'
    if active:
        assert os.path.ismount(cfg['mount'])
        if path.exists() and path.read_text() != wanted:
            raise RuntimeError('Unowned SMB runtime include')
        atomic(path, wanted)
        path.chmod(0o644)
    elif path.exists():
        if path.read_text() != wanted:
            raise RuntimeError('Refusing to remove an unowned SMB include')
        path.unlink()
    run(['testparm', '-s'])
    run(['smbcontrol', 'all', 'reload-config'])
    if not active:
        # Only this trial share reconnects; original Media and other shares stay open.
        run(['smbcontrol', 'smbd', 'close-share', cfg['share']])


def stop_signal(*_):
    global STOP
    STOP = True


def supervise(cfg, cfgpath):
    root = Path(cfg['root'])
    with (root/'owner.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pid = os.getpid()
        atomic(root/'owner.json', {'pid': pid, 'start': Path('/proc/self/stat').read_text().split()[21]})
        signal.signal(signal.SIGTERM, stop_signal)
        signal.signal(signal.SIGINT, stop_signal)
        mount = Path(cfg['mount'])
        mount.mkdir(exist_ok=True)
        if os.path.ismount(mount):
            raise RuntimeError('Mount already exists without this owner')
        mount.chmod(0o000)  # Unmounted backing directory cannot masquerade as an empty library.
        assert Path(cfg['source']).is_dir() and os.path.ismount('/mnt/cache')
        cache = root/'cache'
        cache.mkdir(exist_ok=True)
        empty = root/'empty-rclone.conf'
        if not empty.exists():
            empty.write_bytes(b'')
        assert empty.read_bytes() == b''
        assert shutil.disk_usage(cache).free > cfg['min_free_gib'] * 1024**3
        version = run([cfg['rclone'], 'version']).splitlines()[0]
        assert version == cfg.get('rclone_expected_version', 'rclone v1.75.2')
        argv = [cfg['rclone'], 'mount', cfg['source'], cfg['mount'],
            '--config', str(empty), '--cache-dir', str(cache), '--read-only', '--allow-other',
            '--uid', '99', '--gid', '100', '--umask', '022', '--vfs-cache-mode', 'full',
            '--buffer-size', '0', '--vfs-read-ahead', str(cfg['read_ahead_mib'])+'M',
            '--vfs-cache-max-size', str(cfg['max_cache_gib'])+'G',
            '--vfs-cache-min-free-space', str(cfg['min_free_gib'])+'G',
            '--vfs-cache-max-age', '24h', '--vfs-cache-poll-interval', '10s',
            '--vfs-fast-fingerprint', '--vfs-read-chunk-size', '0', '--dir-cache-time', '30s',
            '--poll-interval', '0', '--attr-timeout', '1s', '--contimeout', '5s', '--timeout', '30s',
            '--rc', '--rc-addr', '127.0.0.1:'+str(cfg['rc_port']), '--rc-no-auth',
            '--log-level', 'INFO', '--log-file', str(root/'rclone.log'),
            '--log-file-max-size', '20M', '--log-file-max-backups', '3']
        if cfg.get('read_ahead_low_water_mib', 0):
            argv += ['--vfs-read-ahead-low-water', str(cfg['read_ahead_low_water_mib'])+'M']
        child = None
        status = {'phase': 'starting', 'checked_at': time.time(), 'config': cfg,
                  'source_bytes': 0, 'errors': 0, 'read_ahead_mib': cfg['read_ahead_mib']}
        atomic(root/'status.json', status)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def rc(method):
            req = urllib.request.Request('http://127.0.0.1:'+str(cfg['rc_port'])+'/'+method,
                                         data=b'{}', headers={'Content-Type': 'application/json'})
            with opener.open(req, timeout=3) as r:
                return json.load(r)
        try:
            child = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                if child.poll() is not None:
                    raise RuntimeError('rclone exited during startup')
                if os.path.ismount(mount):
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('Cache mount readiness timed out')
            assert (mount/'Films').is_dir() and (mount/'TV').is_dir()
            share_path(cfg, True)
            previous_bytes = 0
            previous_time = time.time()
            failures = 0
            while not STOP and child.poll() is None:
                try:
                    vfs, core = rc('vfs/stats'), rc('core/stats')
                    now = time.time()
                    used = vfs.get('diskCache', {}).get('bytesUsed', 0)
                    read_bytes = core.get('bytes', 0)
                    free = shutil.disk_usage(cache).free
                    status = {'phase': 'active', 'checked_at': now, 'pid': pid,
                        'rclone_pid': child.pid, 'read_ahead_mib': cfg['read_ahead_mib'],
                        'read_ahead_low_water_mib': cfg.get('read_ahead_low_water_mib', 0),
                        'max_cache_gib': cfg['max_cache_gib'], 'min_free_gib': cfg['min_free_gib'],
                        'cache_bytes': used, 'ssd_free_bytes': free, 'source_bytes': read_bytes,
                        'source_bytes_per_second': max(0,read_bytes-previous_bytes)/max(.1,now-previous_time),
                        'errors': core.get('errors',0), 'fatal_error': core.get('fatalError',False),
                        'last_error': core.get('lastError',''), 'active_transfers': len(core.get('transferring',[])),
                        'cached_files': vfs.get('diskCache',{}).get('files',0),
                        'space_warning': free < (cfg['min_free_gib']+10)*1024**3,
                        'cache_pressure': used > cfg['max_cache_gib']*.9*1024**3}
                    atomic(root/'status.json', status)
                    history = root/'history.jsonl'
                    if history.exists() and history.stat().st_size > 8*1024**2:
                        older = root/'history.previous.jsonl'
                        os.replace(history, older)
                    with history.open('a') as f:
                        f.write(json.dumps(status)+'\n')
                    previous_bytes, previous_time = read_bytes, now
                    failures = 0
                except (OSError, ValueError) as exc:
                    failures += 1
                    status.update(phase='degraded', checked_at=time.time(), last_error=str(exc))
                    atomic(root/'status.json', status)
                    if failures >= 3:
                        raise RuntimeError('Cache monitoring failed repeatedly; return share to original source')
                for _ in range(cfg['sample_seconds']*5):
                    if STOP or child.poll() is not None:
                        break
                    time.sleep(.2)
            if not STOP:
                raise RuntimeError('Cache worker exited unexpectedly')
        except BaseException as exc:
            status['last_error'] = str(exc)
            raise
        finally:
            # Direct-source SMB fallback exists persistently, including after an Unraid reboot.
            try:
                share_path(cfg, False)
                status['direct_source_fallback'] = True
            finally:
                if child and child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
                if os.path.ismount(mount):
                    run(['fusermount3','-u',str(mount)])
                status.update(phase='direct-source', checked_at=time.time())
                atomic(root/'status.json', status)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('start','stop','status','supervise'))
    p.add_argument('--config',required=True)
    args=p.parse_args()
    cfgpath=Path(args.config).resolve(strict=True)
    cfg=json.loads(cfgpath.read_text())
    root=Path(cfg['root']).resolve(strict=True)
    assert cfgpath.parent==root
    assert cfg['source']=='/mnt/zfs/zfs/Media' and cfg['mount']=='/mnt/plex-read-cache'
    assert cfg['runtime_include']=='/run/plex-read-cache-path.conf'
    assert cfg['share']=='PlexReadCache'
    assert 1<=cfg['read_ahead_mib']<=16384 and 1<=cfg['max_cache_gib']<=1000
    assert 0<=cfg.get('read_ahead_low_water_mib',0)<cfg['read_ahead_mib']
    assert 1<=cfg['min_free_gib']<=1000 and 1<=cfg['sample_seconds']<=300
    if args.action=='supervise':
        supervise(cfg,cfgpath)
    elif args.action=='start':
        existing=owned(root/'owner.json')
        if existing:
            print(json.dumps({'already_running':existing}))
            return
        with (root/'supervisor.log').open('ab') as log:
            child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'supervise','--config',str(cfgpath)],
                                   stdout=log,stderr=log,start_new_session=True)
        for _ in range(100):
            if child.poll() is not None:
                raise RuntimeError('Supervisor failed; inspect its retained log')
            if (root/'status.json').exists():
                s=json.loads((root/'status.json').read_text())
                if s.get('pid')==child.pid and s['phase']=='active':
                    print(json.dumps(s))
                    return
            time.sleep(.2)
        raise RuntimeError('Supervisor readiness not established; inspect existing worker, do not duplicate')
    elif args.action=='stop':
        d=owned(root/'owner.json')
        if d:
            os.kill(d['pid'],signal.SIGTERM)
            for _ in range(150):
                if not owned(root/'owner.json'):
                    break
                time.sleep(.2)
            else:
                raise RuntimeError('Owned service has not stopped')
        assert not os.path.ismount(cfg['mount'])
        print((root/'status.json').read_text())
    else:
        print((root/'status.json').read_text())


if __name__=='__main__':
    main()
