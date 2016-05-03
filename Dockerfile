FROM ubuntu:14.04
MAINTAINER Alexander Kukushkin <alexander.kukushkin@zalando.de>

RUN apt-get update && apt-get install python3-pip curl -y && apt-get clean

ENV USER etcd
ENV HOME /home/${USER}
ENV ETCDVERSION 2.3.1

# Create home directory for etcd
RUN useradd -d ${HOME} -k /etc/skel -s /bin/bash -m ${USER} && chmod 777 ${HOME}

# Install boto
RUN pip3 install boto3

EXPOSE 2379 2380

## Install etcd
RUN curl -L https://github.com/coreos/etcd/releases/download/v${ETCDVERSION}/etcd-v${ETCDVERSION}-linux-amd64.tar.gz | tar xz -C /bin --strip=1 --wildcards --no-anchored etcd etcdctl

COPY etcd.py /bin/etcd.py
COPY scm-source.json /scm-source.json

WORKDIR $HOME
COPY scm-source.json /scm-source.json
USER ${USER}
CMD ["/bin/etcd.py"]
