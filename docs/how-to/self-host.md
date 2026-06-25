# Self-Host Deployment Guide

This guide is the canonical operator entrypoint for self-hosting GraphClaw.

## Scope

- Launch model: self-host only.
- Distribution at launch: source checkout + Docker Compose.
- Runtime hosting on managed SaaS is out of scope.

## Prerequisites

- Docker Desktop (or Docker Engine) with Compose v2.
- Python 3.12+ (only needed for local CLI/dev workflows).
- A configured `docker/.env` file with real secret values.

## Current Deployment Path (Pre-v0.1.0)

```bash
git clone https://github.com/graphclaw/graphclaw
cd graphclaw
cp docker/.env.example docker/.env
# Fill in docker/.env values before starting services
docker compose -f docker/docker-compose.yml up -d
```

After services are healthy:

- Gateway API docs: http://localhost:8080/docs
- MinIO console: http://localhost:9001

## Planned Release-Pinned Path (v0.1.0)

At release cut, this section is the source of truth for pinned rollout commands.

```bash
git clone https://github.com/graphclaw/graphclaw
cd graphclaw
git checkout v0.1.0
cp docker/.env.example docker/.env
docker compose -f docker/docker-compose.yml up -d
```

Verification checklist (to run at release cut):

1. `docker compose -f docker/docker-compose.yml ps` shows all required services healthy.
2. `curl http://localhost:8080/health` returns `200`.
3. `http://localhost:8080/docs` loads successfully.

## Full-Stack UI + Backend Deployment

To run cockpit together with backend in a single stack, follow the cockpit self-host guide:

- `graphclaw-cockpit/docs/how-to/self-host.md`

## Related Docs

- `docs/how-to/release.md`
- `docs/explanation/versioning.md`
- `docs/explanation/deprecations.md`
- `docs/architecture/06-deployment-local.md`
