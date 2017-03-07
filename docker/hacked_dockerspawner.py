"""
A Spawner for JupyterHub that runs each user's server in a separate docker container
"""

import os
import string
from textwrap import dedent
from concurrent.futures import ThreadPoolExecutor
from pprint import pformat

import docker
from docker.errors import APIError
from docker.utils import kwargs_from_env
from tornado import gen

from escapism import escape
from jupyterhub.spawner import Spawner
from traitlets import (
    Dict,
    Unicode,
    Bool,
    Int,
    Any,
    default
)

from .dockerspawner import DockerSpawner

from .volumenamingstrategy import default_format_volume_name

class HackerSpawner(DockerSpawner):

    hub_ip_connect = Unicode(
        "172.31.73.122",
        config=True,
        help=dedent(
            """
            If set, DockerSpawner will configure the containers to use
            the specified IP to connect the hub api.  This is useful
            when the hub_api is bound to listen on all ports or is
            running inside of a container.
            """
        )
    )

    docker_host = Unicode(
        "172.31.68.193:2376",
        config=True,
        help=dedent(
            """
            If set, this user's notebook server will spawn on the Docker
            engine running on the given host and port.
            """
        )
    )

    _client = None
    @property
    def client(self):
        """single global client instance"""
        cls = self.__class__
        if cls._client is None:
            if self.use_docker_client_env:
                kwargs = kwargs_from_env(
                    assert_hostname=self.tls_assert_hostname
                )
                client = docker.Client(version='auto', **kwargs)
            else:
                if self.tls:
                    tls_config = True
                elif self.tls_verify or self.tls_ca or self.tls_client:
                    tls_config = docker.tls.TLSConfig(
                        client_cert=self.tls_client,
                        ca_cert=self.tls_ca,
                        verify=self.tls_verify,
                        assert_hostname = self.tls_assert_hostname)
                else:
                    tls_config = None

                docker_host = self.docker_host
                client = docker.Client(base_url=docker_host, tls=tls_config, version='auto')
            cls._client = client
        return cls._client

    @gen.coroutine
    def get_container(self):
        self.log.debug("Getting container '%s'", self.container_name)
        try:
            container = yield self.docker(
                'inspect_container', self.container_name
            )
            self.container_id = container['Id']
        except APIError as e:
            if e.response.status_code == 404:
                self.log.info("Container '%s' is gone", self.container_name)
                container = None
                # my container is gone, forget my id
                self.container_id = ''
            elif e.response.status_code == 500:
                self.log.info("Container '%s' is on unhealthy node", self.container_name)
                container = None
                # my container is unhealthy, forget my id
                self.container_id = ''
            else:
                raise
        return container

    @gen.coroutine
    def start(self, image=None, extra_create_kwargs=None,
        extra_start_kwargs=None, extra_host_config=None):
        """Start the single-user server in a docker container. You can override
        the default parameters passed to `create_container` through the
        `extra_create_kwargs` dictionary and passed to `start` through the
        `extra_start_kwargs` dictionary.  You can also override the
        'host_config' parameter passed to `create_container` through the
        `extra_host_config` dictionary.

        Per-instance `extra_create_kwargs`, `extra_start_kwargs`, and
        `extra_host_config` take precedence over their global counterparts.

        """
        container = yield self.get_container()
        if container is None:
            image = image or self.container_image

            # build the dictionary of keyword arguments for create_container
            create_kwargs = dict(
                image=image,
                environment=self.get_env(),
                volumes=self.volume_mount_points,
                name=self.container_name,
                ports=["80:80","443:443","8080:8080","8888:8888"])
            create_kwargs.update(self.extra_create_kwargs)
            if extra_create_kwargs:
                create_kwargs.update(extra_create_kwargs)

            # build the dictionary of keyword arguments for host_config
            host_config = dict(binds=self.volume_binds, links=self.links)

            #if not self.use_internal_ip:
            host_config['port_bindings'] = {8888: 80}

            host_config.update(self.extra_host_config)

            if extra_host_config:
                host_config.update(extra_host_config)

            self.log.debug("Starting host with config: %s", host_config)

            host_config = self.client.create_host_config(**host_config)
            create_kwargs.setdefault('host_config', {}).update(host_config)

            # create the container
            resp = yield self.docker('create_container', **create_kwargs)
            self.container_id = resp['Id']
            self.log.info(
                "Created container '%s' (id: %s) from image %s",
                self.container_name, self.container_id[:7], image)

        else:
            self.log.info(
                "Found existing container '%s' (id: %s)",
                self.container_name, self.container_id[:7])

        # TODO: handle unpause
        self.log.info(
            "Starting container '%s' (id: %s)",
            self.container_name, self.container_id[:7])

        # build the dictionary of keyword arguments for start
        start_kwargs = {}
        start_kwargs.update(self.extra_start_kwargs)
        if extra_start_kwargs:
            start_kwargs.update(extra_start_kwargs)

        # start the container
        yield self.docker('start', self.container_id, **start_kwargs)

        ip, port = yield self.get_ip_and_port()
        # store on user for pre-jupyterhub-0.7:
        self.user.server.ip = ip
        self.user.server.port = port
        # jupyterhub 0.7 prefers returning ip, port:
        return (ip, port)
    
    @gen.coroutine
    def get_ip_and_port(self):
        """Queries Docker daemon for container's IP and port.

        If you are using network_mode=host, you will need to override
        this method as follows::
            
            @gen.coroutine
            def get_ip_and_port(self):
                return self.container_ip, self.container_port

        You will need to make sure container_ip and container_port
        are correct, which depends on the route to the container
        and the port it opens.
        """
        if self.use_internal_ip:
            resp = yield self.docker('inspect_container', self.container_id)
            network_settings = resp['NetworkSettings']
            if 'Networks' in network_settings:
                ip = self.get_network_ip(network_settings)
            else:  # Fallback for old versions of docker (<1.9) without network management
                ip = network_settings['IPAddress']
            port = self.container_port
        else:
            resp = yield self.docker('port', self.container_id, self.container_port)
            if resp is None:
                raise RuntimeError("Failed to get port info for %s" % self.container_id)
            ip = resp[0]['HostIp']
            port = resp[0]['HostPort']
        return "172.31.68.193",80

    def get_network_ip(self, network_settings):
        networks = network_settings['Networks']
        if self.network_name not in networks:
            raise Exception(
                "Unknown docker network '{network}'. Did you create it with 'docker network create <name>' and "
                "did you pass network_mode=<name> in extra_kwargs?".format(
                    network=self.network_name
                )
            )
        network = networks[self.network_name]
        ip = network['IPAddress']
        return "172.31.68.193"

    @gen.coroutine
    def stop(self, now=False):
        """Stop the container

        Consider using pause/unpause when docker-py adds support
        """
        self.log.info(
            "Stopping container %s (id: %s)",
            self.container_name, self.container_id[:7])
        yield self.docker('stop', self.container_id)

        if self.remove_containers:
            self.log.info(
                "Removing container %s (id: %s)",
                self.container_name, self.container_id[:7])
            # remove the container, as well as any associated volumes
            yield self.docker('remove_container', self.container_id, v=True)

        self.clear_state()


