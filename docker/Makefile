include .env

.DEFAULT_GOAL=build

network:
	@docker network inspect $(DOCKER_NETWORK_NAME) >/dev/null 2>&1 || docker network create $(DOCKER_NETWORK_NAME)

volumes:
	@docker volume inspect $(DATA_VOLUME_HOST) >/dev/null 2>&1 || docker volume create --name $(DATA_VOLUME_HOST)

clone-oauthenticator: 
	test -d ./oauthenticator || git clone -b avillachlab https://github.com/hms-dbmi/oauthenticator.git
       
clone-dockerspawner:
	test -d ./dockerspawner || git clone https://github.com/hms-dbmi/dockerspawner.git
	cp hacked_dockerspawner.py dockerspawner/dockerspawner/hacked_dockerspawner.py
    
pull:
	docker pull $(DOCKER_NOTEBOOK_IMAGES)

notebook_image: pull

datadog:
	@docker run -d --name dd-agent -v /var/run/docker.sock:/var/run/docker.sock:ro -v /proc/:/host/proc/:ro -v /sys/fs/cgroup/:/host/sys/fs/cgroup:ro -e API_KEY=$(DATADOG_API_KEY) datadog/docker-dd-agent:latest 

build: clone-oauthenticator clone-dockerspawner network pull volumes
	docker-compose build

up:
	docker-compose up -d

.PHONY: network volumes check-files pull notebook_image build up
