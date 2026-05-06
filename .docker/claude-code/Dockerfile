FROM python:3.12-slim

ARG USERNAME=claude
ARG USER_UID=1001
ARG USER_GID=1001
ARG NODE_VERSION=22

RUN groupadd --gid ${USER_GID} ${USERNAME} \
	&& useradd --uid ${USER_UID} --gid ${USER_GID} -m ${USERNAME}

RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		bash \
		ca-certificates \
		curl \
		git \
		gnupg \
		openssh-client \
	&& curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
	&& apt-get install -y --no-install-recommends nodejs \
	&& rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

USER ${USERNAME}
WORKDIR /workspace

CMD ["claude"]
