import unittest

from etcd import EtcdCluster, EtcdManager, EtcdMember
from mock import Mock, patch
from test_etcd_manager import requests_get, instances


class TestEtcdCluster(unittest.TestCase):

    @patch('requests.get', requests_get)
    @patch('boto3.resource')
    def setUp(self, res):
        res.return_value.instances.filter.return_value = instances()
        self.manager = EtcdManager()
        self.manager.instance_id = 'i-deadbeef3'
        self.manager.region = 'eu-west-1'
        EtcdCluster.REGIONS = ['eu-west-1']
        self.cluster = EtcdCluster(self.manager)
        self.cluster.load_members()
        self.assertFalse(EtcdCluster.is_multiregion())

    @patch('boto3.resource')
    def test_load_members(self, res):
        res.return_value.instances.filter.return_value = instances()
        self.assertEqual(len(self.cluster.members), 4)
        with patch('requests.get', Mock(side_effect=Exception)):
            self.cluster.load_members()

    def test_is_healthy(self):
        url = 'http://ip-127-0-0-22.eu-west-1.compute.internal'
        peer_urls = ['{}:{}'.format(url, EtcdMember.DEFAULT_PEER_PORT)]
        me = EtcdMember({
            'id': 'ifoobari7',
            'name': 'i-sadfjhg',
            'clientURLs': ['{}:{}'.format(url, EtcdMember.DEFAULT_CLIENT_PORT)],
            'peerURLs': peer_urls
        })
        self.assertFalse(self.cluster.is_healthy(me))
        self.cluster.members[-1].instance_id = 'foo'
        self.cluster.members[-1].name = ''
        self.assertFalse(self.cluster.is_healthy(me))

        self.cluster.members[-1].peer_urls = peer_urls
        self.assertTrue(self.cluster.is_healthy(me))
        self.cluster.members.pop()
        self.assertTrue(self.cluster.is_healthy(me))
