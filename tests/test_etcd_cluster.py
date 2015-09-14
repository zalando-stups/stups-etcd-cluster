import boto.ec2
import requests
import unittest

from etcd import EtcdCluster, EtcdManager

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
        self.assertFalse(self.cluster.is_healthy('123'))
        self.cluster.members.pop()
        self.assertTrue(self.cluster.is_healthy('123'))
