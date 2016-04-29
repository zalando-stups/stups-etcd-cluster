FROM alpine
MAINTAINER Alexander Kukushkin <alexander.kukushkin@zalando.de>

ENV USER etcd
ENV HOME /home/${USER}
ENV ETCDVERSION 2.3.1

# Create home directory for etcd
RUN adduser -h ${HOME} -s /bin/bash -S ${USER} && chmod 777 ${HOME}

RUN apk add --no-cache python3 curl ca-certificates bash

## We do all these steps in one command to ensure the build-dependencies do
## not make it into the Docker image
RUN apk add --no-cache --virtual=build-dependencies \
    && curl -L "https://bootstrap.pypa.io/get-pip.py" | python3 \
    && pip3 install boto3 requests \
    && apk del build-dependencies \
    && rm -rf /var/cache/apk/*

## Install etcd
RUN curl -L https://github.com/coreos/etcd/releases/download/v${ETCDVERSION}/etcd-v${ETCDVERSION}-linux-amd64.tar.gz \
    | tar xz -C /tmp \
    && mv /tmp/etcd-v*/etcdctl /bin \
    && mv /tmp/etcd-v*/etcd /bin \
    && rm -rf /tmp/etcd-v*

EXPOSE 2379 2380

COPY etcd.py /bin/etcd.py
COPY scm-source.json /scm-source.json

WORKDIR $HOME
USER ${USER}
CMD ["python3" ,"/bin/etcd.py"]
