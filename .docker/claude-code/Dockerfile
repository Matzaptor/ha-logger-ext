FROM node:22-bookworm-slim

ARG USERNAME=claude
ARG USER_UID=1001
ARG USER_GID=1001

RUN groupadd --gid ${USER_GID} ${USERNAME} \
	&& useradd --uid ${USER_UID} --gid ${USER_GID} -m ${USERNAME}

RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		bash \
		ca-certificates \
		curl \
		git \
		openssh-client \
		python3 \
		python3-venv \
		python3-pip \
	&& rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

USER ${USERNAME}
WORKDIR /workspace

CMD ["claude"]
