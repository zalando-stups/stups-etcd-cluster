import unittest

from etcd import EtcdManager, HouseKeeper
from mock import Mock, patch
from test_etcd_manager import instances, requests_get, requests_delete, MockResponse


def requests_put(url, **kwargs):
    response = MockResponse()
    response.status_code = 201
    return response


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
        self.manager = EtcdManager()
        self.manager.get_my_instance()
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
    @patch('boto3.client')
    def test_update_route53_records(self, cli, res):
        cli.return_value.list_hosted_zones_by_name.return_value = {'HostedZones': [{'Id': '', 'Name': 'test.'}]}
        res.return_value.instances.filter.return_value = instances()
        autoscaling_members = self.manager.get_autoscaling_members()
        self.assertIsNone(self.keeper.update_route53_records(autoscaling_members))
        self.keeper.hosted_zone = 'bla'
        self.assertRaises(Exception, self.keeper.update_route53_records, autoscaling_members)

    @patch('subprocess.Popen', Popen)
    def test_cluster_unhealthy(self):
        self.assertTrue(self.keeper.cluster_unhealthy())

    @patch('logging.exception', Mock(side_effect=Exception))
    @patch('os.kill', Mock())
    @patch('time.sleep', Mock(side_effect=Exception))
    @patch('requests.get', requests_get)
    @patch('requests.put', requests_put)
    @patch('requests.delete', requests_delete)
    @patch('subprocess.Popen', Popen)
    @patch('boto3.resource')
    @patch('boto3.client')
    def test_run(self, cli, res):
        cli.return_value.list_hosted_zones_by_name.return_value = {'HostedZones': [{'Id': '', 'Name': 'test.'}]}
        res.return_value.instances.filter.return_value = instances()
        self.assertRaises(Exception, self.keeper.run)
        self.keeper.manager.etcd_pid = 1
        self.assertRaises(Exception, self.keeper.run)
        self.keeper.is_leader = Mock(side_effect=Exception)
        self.assertRaises(Exception, self.keeper.run)
        with patch('time.sleep', Mock()):
            self.keeper.is_leader = Mock(return_value=False)
            self.keeper.manager.runv2 = True
            self.keeper.cluster_unhealthy = Mock(side_effect=[False, True, False])
            self.assertRaises(Exception, self.keeper.run)
            self.keeper.cluster_unhealthy = Mock(side_effect=[False] + [True]*100)
            self.assertRaises(Exception, self.keeper.run)
