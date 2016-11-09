import unittest

from etcd import EtcdCluster, EtcdManager, EtcdMember
from mock import Mock, patch
from test_etcd_manager import requests_get_multiregion, public_instances


class TestEtcdMultiRegionCluster(unittest.TestCase):

    @patch('requests.get', requests_get_multiregion)
    @patch('boto3.resource')
    def setUp(self, res):
        res.return_value.instances.filter.return_value = public_instances()
        self.manager = EtcdManager()
        self.manager.instance_id = 'i-deadbeef3'
        self.manager.region = 'eu-west-1'
        EtcdCluster.REGIONS = ['eu-west-1', 'eu-central-1']
        self.cluster = EtcdCluster(self.manager)
        self.cluster.load_members()
        self.assertTrue(EtcdCluster.is_multiregion())

    @patch('boto3.resource')
    def test_load_members(self, res):
        res.return_value.instances.filter.return_value = public_instances()
        self.assertEqual(len(self.cluster.members), 7)
        with patch('requests.get', Mock(side_effect=Exception)):
            self.cluster.load_members()

    def test_is_healthy(self):
        me = EtcdMember({
            'id': 'ifoobari0815',
            'name': 'i-sadfjhg',
            'clientURLs': ['http://52.0.0.128:{}'.format(EtcdMember.DEFAULT_CLIENT_PORT)],
            'peerURLs': ['http://52.0.0.128:{}'.format(EtcdMember.DEFAULT_PEER_PORT)],
        })
        self.assertFalse(self.cluster.is_healthy(me))
        self.cluster.members[-1].instance_id = 'foo'
        self.cluster.members[-1].name = ''
        self.assertFalse(self.cluster.is_healthy(me))
        self.cluster.members[-1].peer_urls = ['http://52.0.0.128:2380']
        self.assertTrue(self.cluster.is_healthy(me))
        self.cluster.members.pop()
        self.assertTrue(self.cluster.is_healthy(me))
