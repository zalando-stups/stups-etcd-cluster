import json
import os
import unittest

from etcd import EtcdCluster, EtcdClusterException, EtcdManager, EtcdMember, HouseKeeper, main, sigterm_handler
from mock import Mock, patch


class MockResponse:

    def __init__(self):
        self.status_code = 200
        self.content = '{}'

    def json(self):
        return json.loads(self.content)


def requests_get(url, **kwargs):
    response = MockResponse()
    if url == 'http://127.0.0.7:2379/v2/members':
        response.content = '{"members":[]}'
    elif url == 'http://127.0.0.1:2379/version':
        response.content = '{"etcdserver":"2.3.7","etcdcluster":"2.3.0"}'
    elif url == 'http://127.0.0.3:2379/v2/keys/_upgrade_lock':
        response.status_code = 404
    else:
        response.content = \
            """{"region":"eu-west-1", "instanceId": "i-deadbeef3", "leaderInfo":{"leader":"ifoobari1"},"members":[
{"id":"ifoobari1","name":"i-deadbeef1","peerURLs":["http://ip-127-0-0-1.eu-west-1.compute.internal:2380"],
"clientURLs":["http://127.0.0.1:2379"]},
{"id":"ifoobari2","name":"i-deadbeef2","peerURLs":["http://ip-127-0-0-2.eu-west-1.compute.internal:2380"],
"clientURLs":["http://127.0.0.2:2379"]},
{"id":"ifoobari3","name":"i-deadbeef3","peerURLs":["http://ip-127-0-0-3.eu-west-1.compute.internal:2380"],
"clientURLs":["http://127.0.0.3:2379"]},
{"id":"ifoobari4","name":"i-deadbeef4","peerURLs":["http://ip-127-0-0-4.eu-west-1.compute.internal:2380"],
"clientURLs":[]}]}"""
    return response


def requests_get_multiregion(url, **kwargs):
    response = MockResponse()
    if url == 'http://ec2-52-0-0-128.eu-west-1.compute.amazonaws.com:2379/v2/members':
        response.content = '{"members":[]}'
    elif url == 'http://ec2-52-0-0-41.eu-west-1.compute.amazonaws.com:2379/version':
        response.content = '{"etcdserver":"2.3.7","etcdcluster":"2.3.0"}'
    elif url == 'http://ec2-52-0-0-43.eu-west-1.compute.amazonaws.com:2379/v2/keys/_upgrade_lock':
        response.status_code = 404
    else:
        response.content = \
            """{"region":"eu-west-1", "instanceId": "i-deadbeef3", "leaderInfo":{"leader":"ifoobari1"},"members":[
{"id":"ifoobari1","name":"i-deadbeef1","peerURLs":["http://ec2-52-0-0-41.eu-west-1.compute.amazonaws.com:2380"],
"clientURLs":["http://ec2-52-0-0-41.eu-west-1.compute.amazonaws.com:2379"]},
{"id":"ifoobari2","name":"i-deadbeef2","peerURLs":["http://ec2-52-0-0-42.eu-west-1.compute.amazonaws.com:2380"],
"clientURLs":["http://ec2-52-0-0-42.eu-west-1.compute.amazonaws.com:2379"]},
{"id":"ifoobari3","name":"i-deadbeef3","peerURLs":["http://ec2-52-0-0-43.eu-west-1.compute.amazonaws.com:2380"],
"clientURLs":["http://ec2-52-0-0-43.eu-west-1.compute.amazonaws.com:2379"]},
{"id":"ifoobari4","name":"i-deadbeef4","peerURLs":["http://ec2-52-0-0-44.eu-west-1.compute.amazonaws.com:2380"],
"clientURLs":[]},
{"id":"ifoobari5","name":"i-beefcent1","peerURLs":["http://ec2-54-200-0-41.eu-central-1.compute.amazonaws.com:2380"],
"clientURLs":["http://ec2-54-200-0-41.eu-central-1.compute.amazonaws.com:2379"]},
{"id":"ifoobari6","name":"i-beefcent2","peerURLs":["http://ec2-54-200-0-42.eu-central-1.compute.amazonaws.com:2380"],
"clientURLs":["http://ec2-54-200-0-42.eu-central-1.compute.amazonaws.com:2379"]},
{"id":"ifoobari7","name":"i-beefcent3","peerURLs":["http://ec2-54-200-0-43.eu-central-1.compute.amazonaws.com:2380"],
"clientURLs":["http://ec2-54-200-0-43.eu-central-1.compute.amazonaws.com:2379"]}]}"""
    return response


def requests_get_bad_status(url, **kwargs):
    response = requests_get(url, **kwargs)
    response.status_code = 404
    return response


def requests_get_bad_etcd(url, **kwargs):
    response = requests_get(url, **kwargs)
    if '//169.254.169.254/latest/' not in url:
        response.status_code = 404
    return response


def requests_delete(url, **kwargs):
    response = MockResponse()
    response.status_code = (500 if url.endswith('/v2/members/ifoobari7') else 204)
    return response


class MockReservation:

    def __init__(self, instance):
        self.instances = [instance]


class MockInstance:

    state = {'Code': 16, 'Name': 'running'}

    def __init__(self, id, ip, region='eu-west-1', public_ip=None):
        self.id = id
        self.private_ip_address = ip
        self.private_dns_name = 'ip-{}.{}.compute.internal'.format(ip.replace('.', '-'), region)
        self.public_ip_address = public_ip
        self.public_dns_name = public_ip and \
            'ec2-{}.{}.compute.amazonaws.com'.format(public_ip.replace('.', '-'), region)
        self.tags = [
            {'Key': 'aws:cloudformation:stack-name', 'Value': 'etc-cluster'},
            {'Key': 'aws:autoscaling:groupName', 'Value': 'etc-cluster-postgres'}
        ]


def instances():
    return [
        MockInstance('i-deadbeef1', '127.0.0.1'),
        MockInstance('i-deadbeef2', '127.0.0.2'),
        MockInstance('i-deadbeef3', '127.0.0.3')
    ]


def public_instances():
    return [
        MockInstance('i-deadbeef1', '127.0.0.1', 'eu-west-1', '52.0.0.41'),
        MockInstance('i-deadbeef2', '127.0.0.2', 'eu-west-1', '52.0.0.42'),
        MockInstance('i-deadbeef3', '127.0.0.3', 'eu-west-1', '52.0.0.43'),
        MockInstance('i-beefcent1', '127.0.0.1', 'eu-central-1', '54.200.0.41'),
        MockInstance('i-beefcent2', '127.0.0.2', 'eu-central-1', '54.200.0.42'),
        MockInstance('i-beefcent3', '127.0.0.3', 'eu-central-1', '54.200.0.43')
    ]


class SleepException(Exception):
    pass


class TestEtcdManager(unittest.TestCase):

    @patch('boto3.resource')
    @patch('requests.get', requests_get)
    def setUp(self, res):
        self.manager = EtcdManager()
        res.return_value.instances.filter.return_value = instances()
        self.manager.find_my_instance()

    @patch('boto3.resource')
    def test_get_autoscaling_members(self, res):
        res.return_value.instances.filter.return_value = instances()
        self.assertEqual(len(self.manager.get_autoscaling_members()), 3)
        self.assertEqual(self.manager.instance_id, 'i-deadbeef3')
        self.assertEqual(self.manager.region, 'eu-west-1')

    def test_clean_data_dir(self):
        self.manager.clean_data_dir()
        os.mkdir(self.manager.DATA_DIR)
        self.manager.clean_data_dir()
        open(self.manager.DATA_DIR, 'w').close()
        self.manager.clean_data_dir()
        os.symlink('foo', self.manager.DATA_DIR)
        with patch('os.unlink', Mock(side_effect=Exception)):
            self.manager.clean_data_dir()
        self.manager.clean_data_dir()

    @patch('requests.get', requests_get_bad_status)
    def test_load_my_identities(self):
        self.assertRaises(EtcdClusterException, self.manager.load_my_identities)

    @patch('time.sleep', Mock())
    @patch('requests.get', requests_get)
    @patch('boto3.resource')
    def test_register_me(self, res):
        res.return_value.instances.filter.return_value = instances()
        cluster = EtcdCluster(self.manager)
        cluster.load_members()
        self.manager.me.id = '1'
        self.manager.register_me(cluster)

        self.manager.me.id = None
        cluster.accessible_member.add_member = Mock(return_value=False)
        self.assertRaises(EtcdClusterException, self.manager.register_me, cluster)

        self.manager.me.client_urls = ['a']
        cluster.accessible_member.delete_member = Mock(return_value=False)
        self.assertRaises(EtcdClusterException, self.manager.register_me, cluster)

        cluster.accessible_member.delete_member = cluster.accessible_member.add_member = Mock(return_value=True)
        self.manager.register_me(cluster)

        cluster.leader_id = None
        self.assertRaises(EtcdClusterException, self.manager.register_me, cluster)

        cluster.accessible_member = None
        self.manager.register_me(cluster)

    @patch('boto3.resource')
    @patch('os.path.exists', Mock(return_value=True))
    @patch('os.execv', Mock(side_effect=Exception))
    @patch('os.fork', Mock(return_value=0))
    @patch('time.sleep', Mock(side_effect=SleepException))
    @patch('requests.get', requests_get)
    def test_run(self, res):
        res.return_value.instances.filter.return_value = instances()
        self.assertRaises(SleepException, self.manager.run)

        with patch('os.fork', Mock(return_value=1)):
            with patch('os.waitpid', Mock(return_value=(1, 0))):
                self.assertRaises(SleepException, self.manager.run)
                with patch.object(EtcdCluster, 'load_members', Mock(side_effect=SystemExit)):
                    self.manager.run()


class TestMain(unittest.TestCase):

    def test_sigterm_handler(self):
        self.assertRaises(SystemExit, sigterm_handler, None, None)

    @patch('requests.get', requests_get)
    @patch('requests.delete', requests_delete)
    @patch.object(HouseKeeper, 'start', Mock())
    @patch.object(EtcdMember, 'delete_member', Mock(return_value=False))
    @patch('os.fork', Mock(return_value=1))
    @patch('os.waitpid', Mock(return_value=(1, 0)))
    @patch('time.sleep', Mock(side_effect=SleepException))
    @patch('boto3.resource')
    def test_main(self, res):
        res.return_value.instances.filter.return_value = instances()
        self.assertRaises(SleepException, main)
        with patch('requests.get', requests_get_bad_status):
            self.assertRaises(SleepException, main)
        with patch('requests.get', requests_get_bad_etcd):
            self.assertRaises(SleepException, main)
