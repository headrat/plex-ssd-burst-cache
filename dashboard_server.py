"""Read-only, LAN-only status dashboard for the Plex cache trial."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import time
import fcntl
import os
from cache_delivery import DeliveryMonitor

p=argparse.ArgumentParser()
p.add_argument('--root',required=True,type=Path)
p.add_argument('--bind')
p.add_argument('--allow-network',action='append',help='Explicit trusted client CIDR; repeat for multiple networks')
p.add_argument('--port',type=int,default=18779)
args=p.parse_args()
root=args.root.resolve(strict=True)
try:
    config=json.loads((root/'config.json').read_text())
except (OSError,ValueError):
    config={}
args.bind=args.bind or config.get('dashboard_bind','127.0.0.1')
owner_lock=(root/'dashboard.lock').open('a+b')
fcntl.flock(owner_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
(root/'dashboard-owner.json').write_text(json.dumps({'pid':os.getpid(),'start':Path('/proc/self/stat').read_text().split()[21]}))
html=(root/'dashboard.html').read_bytes()
networks=[ipaddress.ip_network(n) for n in (args.allow_network or [config.get('dashboard_allow_network','127.0.0.0/8')])]
delivery=DeliveryMonitor(root)
delivery.start()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):
        pass
    def do_GET(self):
        address=ipaddress.ip_address(self.client_address[0])
        if not address.is_loopback and not any(address in network for network in networks):
            self.send_error(403)
            return
        path=self.path.split('?',1)[0]
        if path=='/':
            data,kind=html,'text/html; charset=utf-8'
        elif path=='/api/status':
            try:
                value=json.loads((root/'status.json').read_text())
                value={k:v for k,v in value.items() if k not in ('config','pid','rclone_pid')}
                value['stale']=value.get('phase')!='direct-source' and time.time()-value.get('checked_at',0)>30
                value.update(delivery.fields())
            except (OSError,ValueError):
                value={'phase':'unavailable','stale':True}
            data,kind=json.dumps(value).encode(),'application/json'
        elif path=='/api/history':
            values=[]
            served=delivery.samples()
            file=root/'history.jsonl'
            if file.exists():
                with file.open('rb') as f:
                    f.seek(max(0,file.stat().st_size-500000))
                    lines=f.read().splitlines()
                for line in lines[-360:]:
                    try:
                        s=json.loads(line)
                        value={k:s.get(k) for k in ('checked_at','source_bytes_per_second','cache_bytes','errors')}
                        value['cache_delivery_bytes_per_second']=served.get(s.get('checked_at'),{}).get('cache_delivery_bytes_per_second')
                        values.append(value)
                    except ValueError:
                        pass
            data,kind=json.dumps(values).encode(),'application/json'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

ThreadingHTTPServer((args.bind,args.port),Handler).serve_forever()
