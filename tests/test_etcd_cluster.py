import boto.ec2
import requests
import unittest

from etcd import EtcdCluster, EtcdManager, EtcdMember

from test_etcd_manager import requests_get, boto_ec2_connect_to_region


def requests_get_fail(*args, **kwargs):
    raise Exception


class TestEtcdCluster(unittest.TestCase):

    def __init__(self, method_name='runTest'):
        self.setUp = self.set_up
        super(TestEtcdCluster, self).__init__(method_name)

    def set_up(self):
        requests.get = requests_get
        boto.ec2.connect_to_region = boto_ec2_connect_to_region
        self.manager = EtcdManager()
        self.manager.instance_id = 'i-deadbeef3'
        self.manager.region = 'eu-west-1'
        self.cluster = EtcdCluster(self.manager)
        self.cluster.load_members()

    def test_load_members(self):
        self.assertEqual(len(self.cluster.members), 4)
        requests.get = requests_get_fail
        self.cluster.load_members()

    def test_is_healthy(self):
        me = EtcdMember({
            'id': 'ifoobari7',
            'name': 'i-sadfjhg',
            'clientURLs': ['http://127.0.0.2:{}'.format(EtcdMember.DEFAULT_CLIENT_PORT)],
            'peerURLs': ['http://127.0.0.2:{}'.format(EtcdMember.DEFAULT_PEER_PORT)],
        })
        self.assertFalse(self.cluster.is_healthy(me))
        self.cluster.members[-1].instance_id = 'foo'
        self.cluster.members[-1].name = ''
        self.assertFalse(self.cluster.is_healthy(me))
        self.cluster.members[-1].peer_urls = ['http://127.0.0.2:2380']
        self.assertTrue(self.cluster.is_healthy(me))
        self.cluster.members.pop()
        self.assertTrue(self.cluster.is_healthy(me))
