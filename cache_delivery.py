"""Delivery from a dedicated cache SMB share, including filesystem RAM cache.

TCP acknowledged bytes include SMB framing/metadata. This is neither physical
SSD I/O nor the iPad's received playback rate. Mixed data shares are excluded.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time


def socket_counters(payload, share):
    shares = {}
    identities = {}
    for tcon in payload['smb'].get('tcons', {}).values():
        server = tcon['server_id']
        pid = str(server['pid'])
        shares.setdefault(pid, set()).add(tcon['service'].casefold())
        identities[pid] = str(server.get('unique_id', ''))
    candidates = {pid for pid, names in shares.items() if share.casefold() in names}
    eligible = {pid for pid in candidates if shares[pid] <= {share.casefold(), 'ipc$'}}
    counters = {}
    current = None
    for line in payload['tcp'].splitlines():
        match = re.search(r'pid=(\d+)', line)
        if match:
            pid = match.group(1)
            fields = line.split()
            endpoints = next(((fields[i], fields[i+1]) for i in range(len(fields)-1)
                              if fields[i].endswith(':445')), None)
            current = (pid, identities.get(pid), endpoints) if pid in eligible and endpoints else None
        elif current:
            match = re.search(r'\bbytes_acked:(\d+)', line)
            if match:
                counters[current] = int(match.group(1))
            elif re.search(r'\bbytes_sent:', line):
                # Missing acknowledged counter is unknown, never zero delivery.
                return {}, 'counter_unavailable'
            else:
                counters[current] = 0  # Fresh connection with no sent data yet.
            current = None
    if candidates - eligible:
        return {}, 'shared_connection'
    if candidates and not counters:
        return {}, 'counter_unavailable'
    return counters, 'ready'


class DeliveryRate:
    def __init__(self):
        self.previous = None
        self.last_sample = None

    def sample(self, payload, share):
        now = payload['observed_at']
        counters, state = socket_counters(payload, share)
        if self.previous and now == self.previous[0] and self.last_sample:
            return dict(self.last_sample)  # Container status can refresh faster than host telemetry.
        rate = None
        if state == 'ready' and self.previous:
            then, previous = self.previous
            if now > then and all(key in previous and value >= previous[key] for key, value in counters.items()):
                rate = sum(value-previous[key] for key, value in counters.items())/(now-then)
            else:
                state = 'warming_up'
        elif state == 'ready':
            state = 'warming_up'
        self.previous = (now, counters) if state in ('ready', 'warming_up') else None
        self.last_sample = {'cache_delivery_bytes_per_second': rate,
                'cache_delivery_state': state, 'cache_delivery_connections': len(counters),
                'cache_delivery_checked_at': now, 'cache_delivery_method': 'smb_tcp_acked'}
        return dict(self.last_sample)


def native_snapshot(cfg):
    if not os.path.ismount(cfg['mount']):
        raise RuntimeError('Cache mount is inactive')
    include = Path(cfg['runtime_include']).read_text()
    if not any(line.strip() == 'path = '+cfg['mount'] for line in include.splitlines()):
        raise RuntimeError('Share does not point at the cache')
    def command(argv):
        p = subprocess.run(argv, capture_output=True, timeout=2, check=True)
        return p.stdout.decode()
    smb = json.loads(command(['smbstatus', '--json']))
    tcp = command(['ss', '-Htinp', 'state', 'established', '( sport = :445 )'])
    return {'observed_at': time.time(), 'smb': smb, 'tcp': tcp}


class DeliveryMonitor:
    def __init__(self, root):
        self.root = Path(root)
        self.latest = {}
        self.rate = DeliveryRate()
        self.history = self.root/'cache-delivery-history.jsonl'
        self.thread = threading.Thread(target=self.run, daemon=True, name='cache-delivery')

    def start(self):
        self.thread.start()

    def run(self):
        last = None
        while True:
            try:
                status = json.loads((self.root/'status.json').read_text())
                checked = status.get('checked_at')
                if checked != last:
                    last = checked
                    cfg = json.loads((self.root/'config.json').read_text())
                    if status.get('phase') != 'active' or time.time()-checked > 30:
                        raise RuntimeError('Cache is inactive or stale')
                    if cfg.get('mode') == 'container':
                        payload = json.loads((self.root/'host-delivery-input.json').read_text())
                        if time.time()-payload['observed_at'] > 12 or not payload.get('cache_active'):
                            raise RuntimeError('Host delivery sample unavailable')
                        share = payload['share']
                    else:
                        payload = native_snapshot(cfg)
                        share = cfg['share']
                    value = self.rate.sample(payload, share)
                    value['checked_at'] = checked
                    self.latest = value
                    if self.history.exists() and self.history.stat().st_size > 1024**2:
                        os.replace(self.history, self.root/'cache-delivery-history.previous.jsonl')
                    with self.history.open('a') as f:
                        f.write(json.dumps(value)+'\n')
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
                self.latest = {'cache_delivery_bytes_per_second': None,
                               'cache_delivery_state': 'unavailable', 'cache_delivery_checked_at': time.time()}
                self.rate.previous = None
            time.sleep(1)

    def fields(self):
        value = dict(self.latest)
        if time.time()-value.get('cache_delivery_checked_at', 0) > 12:
            return {'cache_delivery_bytes_per_second': None, 'cache_delivery_state': 'unavailable'}
        return {key: val for key, val in value.items() if key != 'checked_at'}

    def samples(self):
        values = {}
        try:
            with self.history.open('rb') as f:
                f.seek(max(0, self.history.stat().st_size-500000))
                lines = f.read().splitlines()
            for line in lines[-400:]:
                try:
                    value = json.loads(line)
                    values[value['checked_at']] = value
                except (ValueError, KeyError):
                    pass
        except OSError:
            pass
        return values
