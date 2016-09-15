[![Build Status](https://travis-ci.org/zalando/stups-etcd-cluster.svg?branch=master)](https://travis-ci.org/zalando/stups-etcd-cluster)
[![Coverage Status](https://coveralls.io/repos/zalando/stups-etcd-cluster/badge.svg?branch=master&service=github)](https://coveralls.io/github/zalando/stups-etcd-cluster?branch=master)

Introduction
============
This etcd appliance is created for an AWS environment. It is available as an etcd cluster internally, for any application willing to use it. For discovery of the appliance we havie a recently updated DNS SRV and A records in a Route53 zone.

Design
======
The appliance supposed to be run on EC2 instances, members of one autoscaling group.
Usage of autoscaling group give us possibility to discover all cluster member via AWS api (python-boto).
Etcd process is executed by python wrapper which is taking care about discovering all members of already existing cluster or the new cluster.
Currently the following scenarios are supported:
- Starting up of the new cluster. etcd.py will figure out that this is the new cluster and run etcd daemon with necessary options.
- If the new EC2 instance is spawned within existing autoscaling group etcd.py will take care about adding this instance into already existing cluster and apply needed options to etcd daemon.
- If something happened with etcd (crached or exited), etcd.py will try to restart it.
- Periodically leader performs cluster health check and remove cluster members which are not members of autoscaling group
- Also it creates or updates SRV and A records in a given zone via AWS api.

Usage
=====

## Step 1: Create an etcd cluster
A cluster can be creating by issuing such a command:

    senza create etcd-cluster.yaml STACK_VERSION HOSTED_ZONE DOCKER_IMAGE

For example, if you made are making an etcd cluster to be used by a service called `foo`, you could issue the following:

    senza create https://raw.github.com/zalando/stups-etcd-cluster/master/etcd-cluster.yaml releaseetcd \
                                   HostedZone=elephant.example.org \
                                   DockerImage=registry.opensource.zalan.do/acid/etcd-cluster:3.0.8-p12

## Step 2: Confirm successful cluster creation
Running this `senza create` command should have created:
- the required amount of EC2 instances
    - with stack name `etcd-cluster`
    - with instance name `etcd-cluster-releaseetcd`
- a security group allowing etcd's ports 2379 and 2380
- a role that allows List and Describe EC2 resources and create records in a Route53
- DNS records
    - an A record of the form `releaseetcd.elephant.example.org.`
    - a SRV record of the form `_etcd-server._tcp.releaseetcd.elephant.example.org.` with port = 2380, i.e. peer port
    - a SRV record of the form `_etcd._tcp.releaseetcd.elephant.example.org.` with port = 2379, i.e. client port

Upgrade
=======
In order to perform a minor or major upgrade without downtime you need to terminate all EC2 instances one-by-one. Between every termination you need to wait at least 5 minutes and monitor cluster-health, logs and DNS records. You should only terminate the next instance if the cluster is healthy again.

To upgrade an existing etcd deployment to 3.0, you must be running 2.3. If you are running a version of etcd before 2.3, you must upgrade to 2.3 (preferably 2.3.7) before upgrading to 3.0.

A major upgrade is possible one version at a time, i.e. it is possible to upgrade from 2.0 to 2.1 and from 2.1 to 2.2, but it is not possible to upgrade from 2.0 to 2.2.

Before 3.0 it was possible simply "join" the new member with a higher major version with the empty data directory to the cluster and it was working fine. Somehow this approach has stopped working for 2.3 -> 3.0 upgrade. So now we are using another technique: if the cluster_version is still 2.3, we are "joining" etcd 2.3.7 member to the cluster, in order to download latest data. When the cluster becomes healthy again, we are taking an "upgrade_lock", stopping etcd 2.3.7 and starting up etcd 3.0. When the cluster is healthy again we are removing "upgrade_lock" in order for other members to upgrade.

The upgrade lock is needed to:
- Temporary switch off "house-keeping" job, which task is removing "unhealthy" members and updating DNS records.
- Make sure that we are upgrading one cluster member at a time.

Demo
====
[![Demo on asciicast](https://asciinema.org/a/32703.png)](https://asciinema.org/a/32703)
