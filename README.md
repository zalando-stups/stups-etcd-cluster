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
                                   DockerImage=registry.opensource.zalan.do/acid/etcd-cluster:2.3.6-p10

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

Demo
====
[![Demo on asciicast](https://asciinema.org/a/32703.png)](https://asciinema.org/a/32703)
