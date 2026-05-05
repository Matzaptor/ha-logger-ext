#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

if [ -f "${ENV_FILE}" ]; then
	set -a
	# shellcheck disable=SC1090
	source "${ENV_FILE}"
	set +a
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
	echo "ANTHROPIC_API_KEY is not set." >&2
	exit 1
fi

export HOST_UID="$(id -u)"
export HOST_GID="$(id -g)"
export ANTHROPIC_API_KEY

cd "${PROJECT_ROOT}"

docker compose -f .docker/claude-code/docker-compose.yml run --rm claude-code