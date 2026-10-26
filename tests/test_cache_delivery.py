import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('cache_delivery',Path(__file__).resolve().parents[1]/'cache_delivery.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def payload(now,amount,other=False,pid='123',port=50000):
    tcons={'1':{'service':'PlexCache','server_id':{'pid':pid,'unique_id':'42'}}}
    if other:tcons['2']={'service':'OtherMedia','server_id':{'pid':pid,'unique_id':'42'}}
    return {'observed_at':now,'smb':{'tcons':tcons},'tcp':f'0 0 192.0.2.1:445 192.0.2.2:{port} users:(("smbd",pid={pid},fd=34))\n bbr bytes_sent:{amount} bytes_acked:{amount} rtt:1\n'}

class DeliveryTests(unittest.TestCase):
    def test_rate_uses_acknowledged_delta_and_actual_interval(self):
        rate=module.DeliveryRate()
        self.assertIsNone(rate.sample(payload(1,900000000),'PlexCache')['cache_delivery_bytes_per_second'])
        sample=rate.sample(payload(6,905242880),'PlexCache')
        self.assertEqual(sample['cache_delivery_bytes_per_second'],1048576)

    def test_mixed_share_not_attributed(self):
        counters,state=module.socket_counters(payload(1,100,other=True),'PlexCache')
        self.assertEqual((counters,state),({},'shared_connection'))

    def test_reconnect_or_reset_does_not_create_a_spike(self):
        for changed in [payload(6,10),payload(6,50000000,port=50001),payload(6,50000000,pid='124')]:
            rate=module.DeliveryRate();rate.sample(payload(1,1000),'PlexCache')
            self.assertIsNone(rate.sample(changed,'PlexCache')['cache_delivery_bytes_per_second'])

    def test_no_connections_is_measured_zero_after_baseline(self):
        rate=module.DeliveryRate()
        empty={'observed_at':1,'smb':{'tcons':{}},'tcp':''}
        rate.sample(empty,'PlexCache');empty['observed_at']=6
        self.assertEqual(rate.sample(empty,'PlexCache')['cache_delivery_bytes_per_second'],0)

    def test_multiple_connections_are_summed(self):
        rate=module.DeliveryRate()
        a=payload(1,100);a['tcp']+=payload(1,200,port=50001)['tcp']
        b=payload(6,600);b['tcp']+=payload(6,1200,port=50001)['tcp']
        rate.sample(a,'PlexCache')
        self.assertEqual(rate.sample(b,'PlexCache')['cache_delivery_bytes_per_second'],300)

    def test_reused_host_snapshot_keeps_rate_until_next_host_sample(self):
        rate=module.DeliveryRate();rate.sample(payload(1,100),'PlexCache')
        a=rate.sample(payload(6,600),'PlexCache')
        b=rate.sample(payload(6,600),'PlexCache')
        self.assertEqual(a,b)
        self.assertEqual(b['cache_delivery_bytes_per_second'],100)

if __name__=='__main__':unittest.main()
