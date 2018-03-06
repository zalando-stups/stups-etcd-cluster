#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import boto3
import json
import logging
import os
import re
import requests
import shutil
import signal
import subprocess
import sys
import time

from threading import Thread

if sys.hexversion >= 0x03000000:
    from urllib.parse import urlparse
else:
    from urlparse import urlparse


class EtcdClusterException(Exception):
    pass


def tags_to_dict(tags):
    return {t['Key']: t['Value'] for t in tags}


class EtcdMember:

    API_TIMEOUT = 3.1
    API_VERSION = '/v2/'
    DEFAULT_CLIENT_PORT = 2379
    DEFAULT_PEER_PORT = 2380
    DEFAULT_METRICS_PORT = 2381
    AG_TAG = 'aws:autoscaling:groupName'
    CF_TAG = 'aws:cloudformation:stack-name'

    def __init__(self, arg, region=None):
        self.id = None  # id of cluster member, could be obtained only from running cluster
        self.name = None  # name of cluster member, always match with the AWS instance.id
        self.instance_id = None  # AWS instance.id
        self.private_ip_address = None
        self.public_ip_address = None
        self.private_dns_name = None
        self.public_dns_name = None
        self._addr = None  # ip addr (private or public) could be assigned only from etcd
        self._dns = None  # hostname (private or public) could be assigned only from etcd
        self.autoscaling_group = None  # Name of autoscaling group (aws:autoscaling:groupName)
        self.cloudformation_stack = None  # Name of cloudformation stack (aws:cloudformation:stack-name)
        self.region = region

        self.client_port = self.DEFAULT_CLIENT_PORT
        self.peer_port = self.DEFAULT_PEER_PORT
        self.metrics_port = self.DEFAULT_METRICS_PORT

        self.client_urls = []  # these values could be assigned only from the running etcd
        self.peer_urls = []  # cluster by performing http://addr:client_port/v2/members api call

        if isinstance(arg, dict):
            self.set_info_from_etcd(arg)
        else:
            self.set_info_from_ec2_instance(arg)

    def set_info_from_ec2_instance(self, instance):
        # by convention member.name == instance.id
        if self.name and self.name != instance.id:
            return

        if self._addr and self._addr not in (instance.private_ip_address, instance.public_ip_address) or \
                self._dns and self._dns not in (instance.private_dns_name, instance.public_dns_name):
            return

        self.instance_id = instance.id
        self.private_ip_address = instance.private_ip_address
        self.public_ip_address = instance.public_ip_address
        self.private_dns_name = instance.private_dns_name
        self.public_dns_name = instance.public_dns_name

        tags = tags_to_dict(instance.tags)
        self.cloudformation_stack = tags[self.CF_TAG]
        self.autoscaling_group = tags[self.AG_TAG]

    @staticmethod
    def get_addr_from_urls(urls):
        for url in urls:
            url = urlparse(url)
            if url and url.netloc:
                return url.hostname
        return None

    def addr_matches(self, peer_urls):
        t = '{0}:' + str(self.peer_port)
        for url in peer_urls:
            url = urlparse(url)
            if url and url.netloc and url.netloc in (t.format(self.private_ip_address),
                                                     t.format(self.public_ip_address),
                                                     t.format(self.private_dns_name),
                                                     t.format(self.public_dns_name)):
                return True
        return False

    def set_info_from_etcd(self, info):
        # by convention member.name == instance.id
        if self.instance_id and info['name'] and self.instance_id != info['name']:
            return

        addr = self.get_addr_from_urls(info['peerURLs'])
        # when you add new member it doesn't have name, but we can match it by peer_addr
        if not addr:
            return
        elif re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', addr):
            if (self.private_ip_address or self.public_ip_address) and \
                    addr not in (self.private_ip_address, self.public_ip_address):
                return
            self._addr = addr
        else:
            if (self.private_dns_name or self.public_dns_name) and \
                    addr not in (self.private_dns_name, self.public_dns_name):
                return
            self._dns = addr

        self.id = info['id']
        self.name = info['name']
        self.client_urls = info['clientURLs']
        self.peer_urls = info['peerURLs']

    @staticmethod
    def generate_url(addr, port):
        return 'http://{}:{}'.format(addr, port)

    def get_client_url(self, endpoint=''):
        url = self.generate_url(self.advertise_addr, self.client_port)
        if endpoint:
            url += self.API_VERSION + endpoint
        return url

    @property
    def addr(self):
        return EtcdCluster.is_multiregion() and self.public_ip_address or self.private_ip_address

    @property
    def dns(self):
        return EtcdCluster.is_multiregion() and self.public_dns_name or self.private_dns_name

    @property
    def advertise_addr(self):
        return EtcdCluster.is_multiregion() and self.public_dns_name or self.private_ip_address

    @property
    def peer_addr(self):
        return '{}:{}'.format(self.dns or self._dns or self._addr, self.peer_port)

    @property
    def peer_url(self):
        return self.peer_urls and self.peer_urls[0] or self.generate_url(self.advertise_addr, self.peer_port)

    def api_get(self, endpoint):
        url = self.get_client_url(endpoint)
        response = requests.get(url, timeout=self.API_TIMEOUT)
        logging.debug('Got response from GET %s: code=%s content=%s', url, response.status_code, response.content)
        return (response.json() if response.status_code == 200 else None)

    def api_put(self, endpoint, data):
        url = self.get_client_url(endpoint)
        response = requests.put(url, data=data)
        logging.debug('Got response from PUT %s %s: code=%s content=%s', url, data, response.status_code,
                      response.content)
        return (response.json() if response.status_code == 201 else None)

    def api_post(self, endpoint, data):
        url = self.get_client_url(endpoint)
        headers = {'Content-type': 'application/json'}
        data = json.dumps(data)
        response = requests.post(url, data=data, headers=headers)
        logging.debug('Got response from POST %s %s: code=%s content=%s', url, data, response.status_code,
                      response.content)
        return (response.json() if response.status_code == 201 else None)

    def api_delete(self, endpoint, data=None):
        url = self.get_client_url(endpoint)
        response = requests.delete(url, data=data)
        logging.debug('Got response from DELETE %s: code=%s content=%s', url, response.status_code, response.content)
        return response.status_code == 204

    def get_cluster_version(self):
        response = requests.get(self.get_client_url() + '/version')
        return response.json()['etcdcluster'] if response.status_code == 200 else None

    def is_leader(self):
        return not self.api_get('stats/leader') is None

    def get_leader(self):
        json = self.api_get('stats/self')
        return (json['leaderInfo']['leader'] if json else None)

    def get_members(self):
        json = self.api_get('members')
        return (json['members'] if json else [])

    def adjust_security_groups(self, action, *members):
        if not EtcdCluster.is_multiregion():
            return

        for region in EtcdCluster.REGIONS:
            ec2 = boto3.resource('ec2', region)
            # stack resource from cloudformation returns the GroupName instat of the GroupID...
            # cloudformation = boto3.resource('cloudformation', region)
            # stack_resource = cloudformation.StackResource(me.cloudformation_stack,
            #                                               'EtcdSecurityGroup')
            # security_group = ec2.SecurityGroup(stack_resource.physical_resource_id)
            # .filter(...) works only with default VPC!
            for sg in ec2.security_groups.all():
                if sg.tags and tags_to_dict(sg.tags).get(self.CF_TAG, '') == self.cloudformation_stack:
                    for m in members:
                        if not m.region or m.region != region:
                            try:
                                getattr(sg, action)(
                                    IpProtocol='tcp',
                                    FromPort=self.client_port,
                                    ToPort=self.peer_port,
                                    CidrIp='{}/32'.format(m.addr)
                                )
                            except Exception:
                                logging.exception('Exception on %s for for %s', action, m.addr)

    def add_member(self, member):
        logging.debug('Adding new member %s:%s to cluster', member.instance_id, member.peer_url)
        response = self.api_post('members', {'peerURLs': [member.peer_url]})
        if response:
            member.set_info_from_etcd(response)
            return True
        return False

    def delete_member(self, member):
        logging.debug('Removing member %s from cluster', member.id)
        result = self.api_delete('members/' + member.id)
        self.adjust_security_groups('revoke_ingress', member)
        return result

    def etcd_arguments(self, data_dir, initial_cluster, cluster_state, run_old)):
        # common flags that always have to be set
        arguments = [
            '-name',
            self.instance_id,
            '--data-dir',
            data_dir,
            '-listen-peer-urls',
            'http://0.0.0.0:{}'.format(self.peer_port),
            '-initial-advertise-peer-urls',
            self.peer_url,
            '-listen-client-urls',
            'http://0.0.0.0:{}'.format(self.client_port),
            '-advertise-client-urls',
            self.get_client_url(),
            '-initial-cluster',
            initial_cluster,
            '-initial-cluster-token',
            self.cloudformation_stack,
            '-initial-cluster-state',
            cluster_state
        ]

        # this section handles etcd version specific flags
        etcdversion = os.environ.get('ETCDVERSION_PREV' if run_old else 'ETCDVERSION')
        if etcdversion:
            etcdversion = tuple(int(x) for x in etcdversion.split('.'))
            # etcd >= v3.3: serve metrics on an additonal port
            if etcdversion >= (3, 3):
                arguments += [
                    '-listen-metrics-urls',
                    'http://0.0.0.0:{}'.format(self.metrics_port),
                ]

        # return final list of arguments
        return arguments


class EtcdCluster:
    REGIONS = []  # more then one (1) Region if this a Multi-Region-Cluster

    def __init__(self, manager):
        self.manager = manager
        self.accessible_member = None
        self.leader_id = None
        self.cluster_version = None
        self.members = []

    @property
    def is_upgraded(self):
        etcdversion = os.environ.get('ETCDVERSION')
        if etcdversion:
            etcdversion = etcdversion[:etcdversion.rfind('.') + 1]

        return etcdversion and self.cluster_version is not None and self.cluster_version.startswith(etcdversion)

    @staticmethod
    def is_multiregion():
        return len(EtcdCluster.REGIONS) > 1

    @staticmethod
    def merge_member_lists(ec2_members, etcd_members):
        # we can match EC2 instance with single etcd member by comparing 'addr:peer_port'
        peers = {m.peer_addr: m for m in ec2_members}

        # iterate through list of etcd members obtained from running etcd cluster
        for m in etcd_members:
            for peer in peers.values():
                if peer.addr_matches(m['peerURLs']):
                    peer.set_info_from_etcd(m)
                    break
            else:  # when etcd member hasn't been found just add it into list
                m = EtcdMember(m)
                peers[m.peer_addr] = m
        return sorted(peers.values(), key=lambda e: e.instance_id or e.name)

    def load_members(self):
        self.accessible_member = None
        self.leader_id = None
        ec2_members = self.manager.get_autoscaling_members()
        etcd_members = []

        # Try to connect to members of autoscaling_group group and fetch information about etcd-cluster
        for member in ec2_members:
            if member.instance_id != self.manager.instance_id:  # Skip myself
                try:
                    etcd_members = member.get_members()
                    if etcd_members:  # We've found accessible etcd member
                        self.accessible_member = member
                        self.leader_id = member.get_leader()  # Let's ask him about leader of etcd-cluster
                        self.cluster_version = member.get_cluster_version()  # and about cluster-wide etcd version
                        break
                except Exception:
                    logging.exception('Load members from etcd')

        # combine both lists together
        self.members = self.merge_member_lists(ec2_members, etcd_members)

    def is_healthy(self, me):
        """"Check that cluster does not contain members other then from our ASG
        or given EC2 instance is already part of cluster"""

        for m in self.members:
            if m.name == me.instance_id:
                return True
            if not m.instance_id:
                logging.warning('Member id=%s name=%s is not part of ASG', m.id, m.name)
                logging.warning('Will wait until it would be removed from cluster by HouseKeeper job running on leader')
                return False
            if m.id and not m.name and not m.client_urls:
                if me.addr_matches(m.peer_urls):
                    return True
                logging.warning('Member (id=%s peerURLs=%s) is registered but not yet joined', m.id, m.peer_urls)
                return False
        return True


class EtcdManager:

    ETCD_BINARY = '/bin/etcd'
    DATA_DIR = 'data'
    NAPTIME = 30

    def __init__(self):
        self.region = None
        self.instance_id = None
        self.me = None
        self.etcd_pid = 0
        self.run_old = False
        self._access_granted = False

    def load_my_identities(self):
        url = 'http://169.254.169.254/latest/dynamic/instance-identity/document'
        response = requests.get(url)
        if response.status_code != 200:
            raise EtcdClusterException('GET %s: code=%s content=%s', url, response.status_code, response.content)
        json = response.json()
        if not EtcdCluster.is_multiregion():
            EtcdCluster.REGIONS = [json['region']]
        self.region = json['region']
        self.instance_id = json['instanceId']

    def find_my_instance(self):
        if not self.instance_id or not self.region:
            self.load_my_identities()

        conn = boto3.resource('ec2', region_name=self.region)
        for i in conn.instances.filter(Filters=[{'Name': 'instance-id', 'Values': [self.instance_id]}]):
            if i.id == self.instance_id and EtcdMember.CF_TAG in tags_to_dict(i.tags):
                return EtcdMember(i, self.region)

    def get_my_instance(self):
        if not self.me:
            self.me = self.find_my_instance()
        return self.me

    def get_autoscaling_members(self):
        me = self.get_my_instance()
        members = []
        for region in EtcdCluster.REGIONS:
            conn = boto3.resource('ec2', region_name=region)
            for i in conn.instances.filter(Filters=[
                    {'Name': 'tag:{}'.format(EtcdMember.CF_TAG),
                     'Values': [me.cloudformation_stack]}]):
                if (i.state['Name'] == 'running' and
                        tags_to_dict(i.tags).get(EtcdMember.CF_TAG, '') == me.cloudformation_stack):
                    m = EtcdMember(i, region)
                    if self.region == region or m.public_ip_address:
                        members.append(m)

        if not self._access_granted:
            me.adjust_security_groups('authorize_ingress', *members)
            self._access_granted = True
        return members

    def clean_data_dir(self):
        path = self.DATA_DIR
        logging.info('Removing data directory: %s', path)
        try:
            if os.path.islink(path):
                os.unlink(path)
            elif not os.path.exists(path):
                return
            elif os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
        except Exception:
            logging.exception('Can not remove %s', path)

    def register_me(self, cluster):
        cluster_state = 'existing'
        include_ec2_instances = remove_member = add_member = False
        data_exists = os.path.exists(self.DATA_DIR)
        if cluster.accessible_member is None:
            include_ec2_instances = True
            cluster_state = 'existing' if data_exists else 'new'
            logging.info('Cluster does not have accessible member yet, cluster state=%s', cluster_state)
        elif len(self.me.client_urls) > 0:
            remove_member = add_member = not data_exists
            logging.info('My clientURLs list is not empty: %s', self.me.client_urls)
            logging.info('My data directory exists=%s', data_exists)
        else:
            if self.me.id:
                cluster_state = 'new' if self.me.name else 'existing'
                logging.info('Cluster state=%s because my(id=%s, name=%s)', cluster_state, self.me.id, self.me.name)
            else:
                add_member = True
                logging.info('add_member = True because I am not part of cluster yet')
            self.clean_data_dir()

        if add_member or remove_member:
            if not cluster.leader_id:
                raise EtcdClusterException('Etcd cluster does not have leader yet. Can not add myself')
            if remove_member:
                if not cluster.accessible_member.delete_member(self.me):
                    raise EtcdClusterException('Can not remove my old instance from etcd cluster')
                time.sleep(self.NAPTIME)
            if add_member:
                if not cluster.accessible_member.add_member(self.me):
                    raise EtcdClusterException('Can not register myself in etcd cluster')
                time.sleep(self.NAPTIME)

        self.run_old = add_member and cluster_state == 'existing' and not cluster.is_upgraded

        peers = ','.join(['{}={}'.format(m.instance_id or m.name, m.peer_url) for m in cluster.members
                         if (include_ec2_instances and m.instance_id) or m.peer_urls])

        return self.me.etcd_arguments(self.DATA_DIR, peers, cluster_state, self.run_old)

    def run(self):
        cluster = EtcdCluster(self)
        while True:
            try:
                cluster.load_members()

                self.me = ([m for m in cluster.members if m.instance_id == self.me.instance_id] or [self.me])[0]

                if cluster.is_healthy(self.me):
                    args = self.register_me(cluster)
                    binary = self.ETCD_BINARY + ('.old' if self.run_old else '')

                    self.etcd_pid = os.fork()
                    if self.etcd_pid == 0:
                        os.execv(binary, [binary] + args)

                    logging.info('Started new %s process with pid: %s and args: %s', binary, self.etcd_pid, args)
                    pid, status = os.waitpid(self.etcd_pid, 0)
                    logging.warning('Process %s finished with exit code %s', pid, status >> 8)
                    self.etcd_pid = 0
            except SystemExit:
                break
            except Exception:
                logging.exception('Exception in main loop')
            logging.warning('Sleeping %s seconds before next try...', self.NAPTIME)
            time.sleep(self.NAPTIME)


class HouseKeeper(Thread):

    NAPTIME = 30

    def __init__(self, manager, hosted_zone):
        super(HouseKeeper, self).__init__()
        self.daemon = True
        self.manager = manager
        self.hosted_zone = hosted_zone
        if hosted_zone:
            self.hosted_zone = hosted_zone.rstrip('.') + '.'
        self.members = {}
        self.unhealthy_members = {}

    def is_leader(self):
        return self.manager.me.is_leader()

    def acquire_lock(self):
        data = {'value': self.manager.instance_id, 'ttl': self.NAPTIME, 'prevExist': False}
        return self.manager.me.api_put('keys/_self_maintenance_lock', data=data) is not None

    def take_upgrade_lock(self, ttl):
        data = {'value': self.manager.instance_id, 'ttl': ttl, 'prevExist': False}
        return self.manager.me.api_put('keys/_upgrade_lock', data=data) is not None

    def release_upgrade_lock(self):
        return self.manager.me.api_delete('keys/_upgrade_lock', data={'value': self.manager.instance_id})

    def check_upgrade_lock(self):
        return self.manager.me.api_get('keys/_upgrade_lock') is not None

    def members_changed(self):
        old_members = self.members.copy()
        new_members = self.manager.me.get_members()
        if all(old_members.pop(m['id'], None) == m for m in new_members) and not old_members:
            return False
        self.members = {m['id']: m for m in new_members}
        return True

    def cluster_unhealthy(self):
        process = subprocess.Popen([self.manager.ETCD_BINARY + 'ctl', 'cluster-health'],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        ret = any('unhealthy' in line or 'unreachable' in line for line in map(str, process.stdout))
        process.wait()
        return ret

    def remove_unhealthy_members(self, autoscaling_members):
        for etcd_member in self.members.values():
            for ec2_member in autoscaling_members:
                if ec2_member.addr_matches(etcd_member['peerURLs']):
                    break
            else:
                self.manager.me.delete_member(EtcdMember(etcd_member))

    def update_record(self, conn, zone_id, rtype, rname, new_value):
        conn.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={
                'Changes': [
                    {
                        'Action': 'UPSERT',
                        'ResourceRecordSet': {
                            'Name': rname,
                            'Type': rtype,
                            'TTL': 60,
                            'ResourceRecords': new_value,
                        }
                    }
                ]
            }
        )

    def update_route53_records(self, autoscaling_members):
        conn = boto3.client('route53', region_name=self.manager.region)
        zones = conn.list_hosted_zones_by_name(DNSName=self.hosted_zone)
        zone = ([z for z in zones['HostedZones'] if z['Name'] == self.hosted_zone] or [None])[0]
        if not zone:
            raise Exception('Failed to find hosted_zone {}'.format(self.hosted_zone))
        zone_id = zone['Id']

        stack_version = self.manager.me.cloudformation_stack.split('-')[-1]

        members = []
        for ec2_member in autoscaling_members:
            for etcd_member in self.members.values():
                if ec2_member.addr_matches(etcd_member['peerURLs']):
                    members.append(ec2_member)
                    break

        record_name = '_etcd-server._tcp.{}.{}'.format(stack_version, self.hosted_zone)
        new_record = [{'Value': ' '.join(map(str, [1, 1, i.peer_port, i.dns]))} for i in members]
        self.update_record(conn, zone_id, 'SRV', record_name, new_record)

        record_name = '_etcd-client._tcp.{}.{}'.format(stack_version, self.hosted_zone)
        new_record = [{'Value': ' '.join(map(str, [1, 1, i.client_port, i.dns]))} for i in members]
        self.update_record(conn, zone_id, 'SRV', record_name, new_record)

        new_record = [{'Value': i.addr} for i in members]
        self.update_record(conn, zone_id, 'A', 'etcd-server.{}.{}'.format(stack_version, self.hosted_zone), new_record)

    def run(self):
        update_required = False
        while True:
            try:
                if self.manager.etcd_pid != 0 and self.is_leader():
                    if (update_required or self.members_changed() or self.cluster_unhealthy()) \
                            and not self.check_upgrade_lock() and self.acquire_lock():
                        update_required = True
                        autoscaling_members = self.manager.get_autoscaling_members()
                        if autoscaling_members:
                            self.remove_unhealthy_members(autoscaling_members)
                            self.update_route53_records(autoscaling_members)
                            update_required = False
                else:
                    self.members = {}
                    update_required = False
                    if self.manager.etcd_pid != 0 and self.manager.run_old \
                            and not self.cluster_unhealthy() and self.take_upgrade_lock(600):
                        logging.info('Performing upgrade of member %s', self.manager.me.name)
                        os.kill(self.manager.etcd_pid, signal.SIGTERM)
                        for _ in range(0, 59):
                            time.sleep(10)
                            if self.cluster_unhealthy():
                                logging.info('upgrade: cluster is unhealthy...')
                            else:
                                logging.info('upgrade complete, removing upgrade lock')
                                self.release_upgrade_lock()
                                break
                        else:
                            logging.error('upgrade: giving up...')
            except Exception:
                logging.exception('Exception in HouseKeeper main loop')
            logging.debug('Sleeping %s seconds...', self.NAPTIME)
            time.sleep(self.NAPTIME)


__ignore_sigterm = False


def sigterm_handler(signo, stack_frame):
    global __ignore_sigterm
    if not __ignore_sigterm:
        __ignore_sigterm = True
        sys.exit()


def main():
    signal.signal(signal.SIGTERM, sigterm_handler)
    logging.basicConfig(format='%(levelname)-6s %(asctime)s - %(message)s', level=logging.INFO)
    hosted_zone = os.environ.get('HOSTED_ZONE', None)
    if os.environ.get('ACTIVE_REGIONS', '') != '':
        EtcdCluster.REGIONS = os.environ.get('ACTIVE_REGIONS').split(',')

    manager = EtcdManager()
    try:
        house_keeper = HouseKeeper(manager, hosted_zone)
        house_keeper.start()
        manager.run()
    finally:
        logging.info('Trying to remove myself from cluster...')
        try:
            cluster = EtcdCluster(manager)
            cluster.load_members()
            if cluster.accessible_member:
                if [m for m in cluster.members if m.name == manager.me.instance_id]\
                        and not cluster.accessible_member.delete_member(manager.me):
                    logging.error('Can not remove myself from cluster')
            else:
                logging.error('Cluster does not have accessible member')
        except Exception:
            logging.exception('Failed to remove myself from cluster')


if __name__ == '__main__':
    main()
