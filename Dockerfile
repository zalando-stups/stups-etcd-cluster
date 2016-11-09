FROM ubuntu:16.04
MAINTAINER Alexander Kukushkin <alexander.kukushkin@zalando.de>

ENV USER etcd
ENV HOME /home/${USER}

# Create home directory for etcd
RUN useradd -d ${HOME} -k /etc/skel -s /bin/bash -m ${USER} && chmod 777 ${HOME}

RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update \
    && echo 'APT::Install-Recommends "0";' > /etc/apt/apt.conf.d/01norecommend \
    && echo 'APT::Install-Suggests "0";' >> /etc/apt/apt.conf.d/01norecommend \

    && apt-get upgrade -y \
    && apt-get install -y curl ca-certificates python3-boto3 \

    # Clean up
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

## Install etcd
ENV ETCDVERSION 2.3.7
RUN curl -L https://github.com/coreos/etcd/releases/download/v${ETCDVERSION}/etcd-v${ETCDVERSION}-linux-amd64.tar.gz | tar xz -C /bin --xform='s/$/v2/x' --strip=1 --wildcards --no-anchored etcd

ENV ETCDVERSION 3.0.14
RUN curl -L https://github.com/coreos/etcd/releases/download/v${ETCDVERSION}/etcd-v${ETCDVERSION}-linux-amd64.tar.gz | tar xz -C /bin --strip=1 --wildcards --no-anchored etcd etcdctl

COPY etcd.py /bin/etcd.py
COPY scm-source.json /scm-source.json

WORKDIR $HOME
USER ${USER}
EXPOSE 2379 2380
CMD ["/usr/bin/python3", "/bin/etcd.py"]
