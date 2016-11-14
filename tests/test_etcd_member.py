import json
import unittest

from etcd import EtcdMember
from mock import patch, Mock
from test_etcd_manager import requests_delete, requests_get, MockInstance, MockResponse


def requests_post(url, **kwargs):
    response = MockResponse()
    data = json.loads(kwargs['data'])
    if data['peerURLs'][0] in ['http://ip-127-0-0-2.eu-west-1.compute.internal:2380',
                               'http://ip-127-0-0-3.eu-west-1.compute.internal:2380']:
        response.status_code = 201
        response.content = '{"id":"ifoobar","name":"","peerURLs":["' + data['peerURLs'][0] + '"],"clientURLs":[""]}'
    else:
        response.status_code = 403
    return response


class TestEtcdMember(unittest.TestCase):

    def setUp(self):
        self.ec2 = MockInstance('i-foobar', '127.0.0.1')
        self.ec2_member = EtcdMember(self.ec2)
        self.etcd = {
            'id': 'deadbeef',
            'name': 'i-foobar2',
            'clientURLs': [],
            'peerURLs': ['http://ip-127-0-0-2.eu-west-1.compute.internal:{}'.format(EtcdMember.DEFAULT_PEER_PORT)],
        }
        self.etcd_member = EtcdMember(self.etcd)

    def test_get_addr_from_urls(self):
        self.assertEqual(self.ec2_member.get_addr_from_urls(['http://1.2:3']), '1.2')
        self.assertEqual(self.ec2_member.get_addr_from_urls(['http://1.2']), '1.2')
        self.assertIsNone(self.ec2_member.get_addr_from_urls(['http//1.2']))

    def test_set_info_from_ec2_instance(self):
        self.assertEqual(self.etcd_member.dns, 'ip-127-0-0-2.eu-west-1.compute.internal')
        self.etcd_member.set_info_from_ec2_instance(self.ec2)
        self.etcd_member.name = ''
        self.etcd_member.set_info_from_ec2_instance(self.ec2)

    def test_set_info_from_etcd(self):
        self.ec2_member.set_info_from_etcd(self.etcd)
        self.etcd['name'] = 'i-foobar'
        self.ec2_member.set_info_from_etcd(self.etcd)
        self.etcd['name'] = 'i-foobar2'

    @patch('requests.post', requests_post)
    def test_add_member(self):
        member = EtcdMember({
            'id': '',
            'name': '',
            'clientURLs': [],
            'peerURLs': ['http://ip-127-0-0-2.eu-west-1.compute.internal:{}'.format(EtcdMember.DEFAULT_PEER_PORT)],
        })
        self.assertTrue(self.ec2_member.add_member(member))
        member.dns = 'ip-127-0-0-4.eu-west-1.compute.internal'
        self.assertFalse(self.ec2_member.add_member(member))

    @patch('requests.get', requests_get)
    def test_is_leader(self):
        self.assertTrue(self.ec2_member.is_leader())

    @patch('boto3.resource')
    @patch('requests.delete', requests_delete)
    def test_delete_member(self, res):
        sg = Mock()
        sg.tags = [
            {'Key': 'aws:cloudformation:stack-name', 'Value': 'etc-cluster'},
            {'Key': 'aws:autoscaling:groupName', 'Value': 'etc-cluster-postgres'}
        ]
        sg.revoke_ingress.side_effect = Exception
        res.return_value.security_groups.all.return_value = [sg]
        member = EtcdMember({
            'id': 'ifoobari7',
            'name': 'i-sadfjhg',
            'clientURLs': ['http://ip-127-0-0-2.eu-west-1.compute.internal:{}'.format(EtcdMember.DEFAULT_CLIENT_PORT)],
            'peerURLs': ['http://ip-127-0-0-2.eu-west-1.compute.internal:{}'.format(EtcdMember.DEFAULT_PEER_PORT)]
        })
        member.addr = '127.0.0.1'
        self.assertFalse(self.ec2_member.delete_member(member))

    @patch('requests.get', requests_get)
    def test_get_leader(self):
        self.ec2_member.dns = 'ip-127-0-0-7.eu-west-1.compute.internal'
        self.assertEqual(self.ec2_member.get_leader(), 'ifoobari1')

    @patch('requests.get', requests_get)
    def test_get_members(self):
        self.ec2_member.dns = 'ip-127-0-0-7.eu-west-1.compute.internal'
        self.assertEqual(self.ec2_member.get_members(), [])
