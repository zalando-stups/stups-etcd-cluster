#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import boto.ec2
import boto.route53
import json
import logging
import os
import requests
import shutil
import signal
import subprocess
import sys
import time

from boto.ec2.instance import Instance
from threading import Thread

if sys.hexversion >= 0x03000000:
    from urllib.parse import urlparse
else:
    from urlparse import urlparse


class EtcdClusterException(Exception):
    pass


class EtcdMember:

    API_TIMEOUT = 3.1
    API_VERSION = '/v2/'
    DEFAULT_CLIENT_PORT = 2379
    DEFAULT_PEER_PORT = 2380
    AG_TAG = 'aws:autoscaling:groupName'

    def __init__(self, arg):
        self.id = None  # id of cluster member, could be obtained only from running cluster
        self.name = None  # name of cluster member, always match with the AWS instance.id
        self.instance_id = None  # AWS instance.id
        self.addr = None  # private ip address of the instance or peer_addr
        self.cluster_token = None  # match with aws:cloudformation:stack-name
        self.autoscaling_group = None  # Name of autoscaling group (aws:autoscaling:groupName)

        self.client_port = self.DEFAULT_CLIENT_PORT
        self.peer_port = self.DEFAULT_PEER_PORT

        self.client_urls = []  # these values could be assigned only from the running etcd
        self.peer_urls = []  # cluster by performing http://addr:client_port/v2/members api call

        if isinstance(arg, Instance):
            self.set_info_from_ec2_instance(arg)
        else:
            self.set_info_from_etcd(arg)

    def set_info_from_ec2_instance(self, instance):
        # by convention member.name == instance.id
        if self.name and self.name != instance.id:
            return

        # when you add new member it doesn't have name, but we can match it by peer_addr
        if self.addr and self.addr != instance.private_ip_address:
            return

        self.instance_id = instance.id
        self.addr = instance.private_ip_address
        self.dns = instance.private_dns_name
        self.cluster_token = instance.tags['aws:cloudformation:stack-name']
        self.autoscaling_group = instance.tags[self.AG_TAG]

    @staticmethod
    def get_addr_from_urls(urls):
        for url in urls:
            url = urlparse(url)
            if url and url.netloc:
                # TODO: check that hostname contains ip
                return url.hostname
        return None

    def set_info_from_etcd(self, info):
        # by convention member.name == instance.id
        if self.instance_id and info['name'] and self.instance_id != info['name']:
            return

        addr = self.get_addr_from_urls(info['peerURLs'])
        # when you add new member it doesn't have name, but we can match it by peer_addr
        if self.addr and (not addr or self.addr != addr):
            return

        self.id = info['id']
        self.name = info['name']
        self.client_urls = info['clientURLs']
        self.peer_urls = info['peerURLs']
        self.addr = addr

    @staticmethod
    def generate_url(addr, port):
        return 'http://{}:{}'.format(addr, port)

    def get_client_url(self, endpoint=''):
        url = self.generate_url(self.addr, self.client_port)
        if endpoint:
            url += self.API_VERSION + endpoint
        return url

    @property
    def peer_addr(self):
        return '{}:{}'.format(self.addr, self.peer_port)

    @property
    def peer_url(self):
        return self.generate_url(self.addr, self.peer_port)

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

    def api_delete(self, endpoint):
        url = self.get_client_url(endpoint)
        response = requests.delete(url)
        logging.debug('Got response from DELETE %s: code=%s content=%s', url, response.status_code, response.content)
        return response.status_code == 204

    def is_leader(self):
        return not self.api_get('stats/leader') is None

    def get_leader(self):
        json = self.api_get('stats/self')
        return (json['leaderInfo']['leader'] if json else None)

    def get_members(self):
        json = self.api_get('members')
        return (json['members'] if json else [])

    def add_member(self, member):
        logging.debug('Adding new member %s:%s to cluster', member.instance_id, member.peer_url)
        response = self.api_post('members', {'peerURLs': [member.peer_url]})
        if response:
            member.set_info_from_etcd(response)
            return True
        return False

    def delete_member(self, member):
        logging.debug('Removing member %s from cluster', member.id)
        return self.api_delete('members/' + member.id)

    def etcd_arguments(self, data_dir, initial_cluster, cluster_state):
        return [
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
            self.cluster_token,
            '-initial-cluster-state',
            cluster_state,
        ]


class EtcdCluster:

    def __init__(self, manager):
        self.manager = manager
        self.accessible_member = None
        self.leader_id = None
        self.members = []

    @staticmethod
    def merge_member_lists(ec2_members, etcd_members):
        # we can match EC2 instance with single etcd member by comparing 'addr:peer_port'
        peers = {m.peer_addr: m for m in ec2_members}

        # iterate through list of etcd members obtained from running etcd cluster
        for m in etcd_members:
            for peer_url in m['peerURLs']:
                r = urlparse(peer_url)
                if r.netloc in peers:  # etcd member found among list of EC2 instances
                    peers[r.netloc].set_info_from_etcd(m)
                    m = None
                    break

            # when etcd member hasn't been found just add it into list
            if m:
                m = EtcdMember(m)
                peers[m.peer_addr] = m
        return sorted(peers.values(), key=lambda e: e.instance_id or e.name)

    def load_members(self):
        self.accessible_member = None
        self.leader_id = None
        ec2_members = list(map(EtcdMember, self.manager.get_autoscaling_members()))
        etcd_members = []

        # Try to connect to members of autoscaling_group group and fetch information about etcd-cluster
        for member in ec2_members:
            if member.instance_id != self.manager.instance_id:  # Skip myself
                try:
                    etcd_members = member.get_members()
                    if etcd_members:  # We've found accessible etcd member
                        self.accessible_member = member
                        self.leader_id = member.get_leader()  # Let's ask him about leader of etcd-cluster
                        break
                except:
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
                # go through list of peerURLs and try to find my instance there
                for peer_url in m.peer_urls:
                    r = urlparse(peer_url)
                    if r.netloc == me.peer_addr:
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

    def load_my_identities(self):
        url = 'http://169.254.169.254/latest/dynamic/instance-identity/document'
        response = requests.get(url)
        if response.status_code != 200:
            raise EtcdClusterException('GET %s: code=%s content=%s', url, response.status_code, response.content)
        json = response.json()
        self.region = json['region']
        self.instance_id = json['instanceId']

    def find_my_instance(self):
        if not self.instance_id or not self.region:
            self.load_my_identities()

        conn = boto.ec2.connect_to_region(self.region)
        for r in conn.get_all_reservations(filters={'instance_id': self.instance_id}):
            for i in r.instances:
                if i.id == self.instance_id and EtcdMember.AG_TAG in i.tags:
                    return EtcdMember(i)

    def get_my_instance(self):
        if not self.me:
            self.me = self.find_my_instance()
        return self.me

    def get_autoscaling_members(self):
        me = self.get_my_instance()

        conn = boto.ec2.connect_to_region(self.region)
        res = conn.get_all_reservations(filters={'tag:{}'.format(EtcdMember.AG_TAG): me.autoscaling_group})

        return [i for r in res for i in r.instances if i.state != 'terminated' and i.tags.get(EtcdMember.AG_TAG, '')
                == me.autoscaling_group]

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
        except:
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

        peers = ','.join(['{}={}'.format(m.instance_id or m.name, m.peer_url) for m in cluster.members
                         if (include_ec2_instances and m.instance_id) or m.peer_urls])

        return self.me.etcd_arguments(self.DATA_DIR, peers, cluster_state)

    def run(self):
        cluster = EtcdCluster(self)
        while True:
            try:
                cluster.load_members()

                self.me = ([m for m in cluster.members if m.instance_id == self.me.instance_id] or [self.me])[0]

                if cluster.is_healthy(self.me):
                    args = self.register_me(cluster)

                    self.etcd_pid = os.fork()
                    if self.etcd_pid == 0:
                        os.execv(self.ETCD_BINARY, [self.ETCD_BINARY] + args)

                    logging.info('Started new etcd process with pid: %s and args: %s', self.etcd_pid, args)
                    pid, status = os.waitpid(self.etcd_pid, 0)
                    logging.warning('Process %s finished with exit code %s', pid, status >> 8)
                    self.etcd_pid = 0
            except SystemExit:
                break
            except:
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
        self.members = {}
        self.unhealthy_members = {}

    def is_leader(self):
        return self.manager.me.is_leader()

    def acquire_lock(self):
        data = data = {'value': self.manager.instance_id, 'ttl': self.NAPTIME, 'prevExist': False}
        return not self.manager.me.api_put('keys/_self_maintenance_lock', data=data) is None

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
        ret = any('is unhealthy' in str(line) or 'is unreachable' in str(line) for line in process.stdout)
        process.wait()
        return ret

    def remove_unhealthy_members(self, autoscaling_members):
        members = {m.addr: m for m in map(EtcdMember, self.members.values())}

        for m in autoscaling_members:
            members.pop(m.private_ip_address, None)

        for m in members.values():
            self.manager.me.delete_member(m)

    @staticmethod
    def update_record(zone, record_type, record_name, new_value):
        records = zone.get_records()
        old_records = [r for r in records if r.type.upper() == record_type and r.name.lower().startswith(record_name)]

        if len(old_records) == 0:
            return zone.add_record(record_type, record_name, new_value)

        if set(old_records[0].resource_records) != set(new_value):
            return zone.update_record(old_records[0], new_value)

    def update_route53_records(self, autoscaling_members):
        conn = boto.route53.connect_to_region('universal')
        zone = conn.get_zone(self.hosted_zone)
        if not zone:
            return

        stack_version = self.manager.me.cluster_token.split('-')[-1]
        members = {m.addr: m for m in map(EtcdMember, self.members.values())}

        new_record = [' '.join(map(str, [1, 1, members[i.private_ip_address].peer_port, i.private_dns_name])) for i in
                      autoscaling_members if i.private_ip_address in members]
        self.update_record(zone, 'SRV', '_etcd-server._tcp.{}.{}'.format(stack_version, self.hosted_zone), new_record)

        new_record = [' '.join(map(str, [1, 1, members[i.private_ip_address].client_port, i.private_dns_name])) for i in
                      autoscaling_members if i.private_ip_address in members]
        self.update_record(zone, 'SRV', '_etcd._tcp.{}.{}'.format(stack_version, self.hosted_zone), new_record)

        new_record = [i.private_ip_address for i in autoscaling_members if i.private_ip_address in members]
        self.update_record(zone, 'A', 'etcd-server.{}.{}'.format(stack_version, self.hosted_zone), new_record)

    def run(self):
        update_required = False
        while True:
            try:
                if self.manager.etcd_pid != 0 and self.is_leader():
                    if (update_required or self.members_changed() or self.cluster_unhealthy()) and self.acquire_lock():
                        update_required = True
                        autoscaling_members = self.manager.get_autoscaling_members()
                        if autoscaling_members:
                            self.remove_unhealthy_members(autoscaling_members)
                            self.update_route53_records(autoscaling_members)
                            update_required = False
                else:
                    self.members = {}
                    update_required = False
            except:
                logging.exception('Exception in HouseKeeper main loop')
            logging.debug('Sleeping %s seconds...', self.NAPTIME)
            time.sleep(self.NAPTIME)


def sigterm_handler(signo, stack_frame):
    sys.exit()


def main():
    signal.signal(signal.SIGTERM, sigterm_handler)
    logging.basicConfig(format='%(levelname)-6s %(asctime)s - %(message)s', level=logging.DEBUG)
    hosted_zone = os.environ.get('HOSTED_ZONE', None)
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
        except:
            logging.exception('Failed to remove myself from cluster')


if __name__ == '__main__':
    main()
