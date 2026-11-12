#!/usr/bin/env bash
set -euo pipefail
# Development only: PostgreSQL is reachable on loopback, without a password.
# Use the attached managed Docker daemon rather than an inherited remote context.
unset DOCKER_HOST DOCKER_CONTEXT DOCKER_TLS DOCKER_TLS_VERIFY DOCKER_CERT_PATH
if docker --host=unix:///var/run/docker.sock container inspect photoshoot-db >/dev/null 2>&1; then
    docker --host=unix:///var/run/docker.sock start photoshoot-db >/dev/null
else
    docker --host=unix:///var/run/docker.sock run -d --name photoshoot-db \
      -e POSTGRES_USER=photoshoot -e POSTGRES_DB=photoshoot \
      -e POSTGRES_HOST_AUTH_METHOD=trust -p 127.0.0.1:5432:5432 \
      -v photoshoot-db-data:/var/lib/postgresql/data postgres:17 >/dev/null
fi
for attempt in {1..60}; do
    if docker --host=unix:///var/run/docker.sock exec photoshoot-db pg_isready -U photoshoot -d photoshoot >/dev/null; then
        echo 'Development PostgreSQL is ready.'
        exit 0
    fi
    sleep 1
done
echo 'PostgreSQL did not become ready within 60 seconds.' >&2
exit 1
