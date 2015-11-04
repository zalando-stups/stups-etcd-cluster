import boto
import unittest

from boto.route53.record import Record
from etcd import EtcdManager, HouseKeeper
from mock import Mock, patch
from test_etcd_manager import instances, requests_get, requests_delete, MockResponse


def requests_put(url, **kwargs):
    response = MockResponse()
    response.status_code = 201
    return response


class MockZone:

    def __init__(self, name):
        self.name = name

    def get_records(self):
        if self.name != 'test.':
            return []
        r = Record()
        r.name = '_etcd-server._tcp.cluster.' + self.name
        r.type = 'SRV'
        return [r]

    def add_record(self, type, name, value):
        pass

    def update_record(self, old, new_value):
        pass


class MockRoute53Connection:

    def get_zone(self, zone):
        return (None if zone == 'bla' else MockZone(zone))


def boto_route53_connect_to_region(region):
    return MockRoute53Connection()


class Popen:

    def __init__(self, args, **kwargs):
        if args[1] != 'cluster-health':
            raise Exception()
        self.stdout = ['cluster is healthy', 'member 15a694aa6a6003f4 is healthy',
                       'member effbc38ed2b11107 is unhealthy']

    def wait(self):
        pass


class TestHouseKeeper(unittest.TestCase):

    @patch('requests.get', requests_get)
    @patch('boto3.resource')
    def setUp(self, res):
        res.return_value.instances.filter.return_value = instances()
        boto.route53.connect_to_region = boto_route53_connect_to_region
        self.manager = EtcdManager()
        self.manager.get_my_instace()
        self.manager.instance_id = 'i-deadbeef3'
        self.manager.region = 'eu-west-1'
        self.keeper = HouseKeeper(self.manager, 'test.')
        self.members_changed = self.keeper.members_changed()

    @patch('requests.get', requests_get)
    def test_members_changed(self):
        self.assertTrue(self.members_changed)
        self.keeper.members['blabla'] = True
        self.assertTrue(self.keeper.members_changed())
        self.assertFalse(self.keeper.members_changed())

    @patch('requests.get', requests_get)
    def test_is_leader(self):
        self.assertTrue(self.keeper.is_leader())

    @patch('requests.put', requests_put)
    def test_acquire_lock(self):
        self.assertTrue(self.keeper.acquire_lock())

    @patch('requests.delete', requests_delete)
    @patch('boto3.resource')
    def test_remove_unhealthy_members(self, res):
        res.return_value.instances.filter.return_value = instances()
        autoscaling_members = self.manager.get_autoscaling_members()
        self.assertIsNone(self.keeper.remove_unhealthy_members(autoscaling_members))

    @patch('boto3.resource')
    def test_update_route53_records(self, res):
        res.return_value.instances.filter.return_value = instances()
        autoscaling_members = self.manager.get_autoscaling_members()
        self.assertIsNone(self.keeper.update_route53_records(autoscaling_members))
        self.keeper.hosted_zone = 'bla'
        self.assertIsNone(self.keeper.update_route53_records(autoscaling_members))
        self.keeper.hosted_zone = 'test2'
        self.assertIsNone(self.keeper.update_route53_records(autoscaling_members))

    @patch('subprocess.Popen', Popen)
    def test_cluster_unhealthy(self):
        self.assertTrue(self.keeper.cluster_unhealthy())

    @patch('time.sleep', Mock(side_effect=Exception))
    @patch('requests.get', requests_get)
    @patch('requests.put', requests_put)
    @patch('requests.delete', requests_delete)
    @patch('subprocess.Popen', Popen)
    @patch('boto3.resource')
    def test_run(self, res):
        res.return_value.instances.filter.return_value = instances()
        self.assertRaises(Exception, self.keeper.run)
        self.keeper.manager.etcd_pid = 1
        self.assertRaises(Exception, self.keeper.run)
        self.keeper.is_leader = Mock(side_effect=Exception)
        self.assertRaises(Exception, self.keeper.run)
