# Investigation: Running OpenSWE Locally with Docker Sandboxes

**Date:** 2026-04-06
**Status:** Research complete, not yet implemented

## Goal

Run the entire OpenSWE stack on a local Mac (or Linux box), with Docker containers
as sandboxes instead of EC2 instances. One OpenSWE instance, multiple simultaneous
sandboxes, no AWS dependency. Use Open WebUI as the chat frontend.

## Current Production Architecture

```
GitHub/Slack webhooks
       |
    ALB (HTTPS)
       |
  ECS Fargate (Aegra server, port 2026)
       |
       +-- RDS PostgreSQL (thread/checkpoint state)
       +-- Secrets Manager (API keys)
       +-- DevPod + AWS provider --> EC2 instances (sandboxes)
       +-- ECR (container images, prebuilds)
```

## Proposed Local Architecture

```
Open WebUI (Docker, port 3000)
       |
       | (pipe function -> Aegra API)
       |
  Aegra server (native or Docker, port 2026)
       |
       +-- PostgreSQL (Docker, port 5432)
       +-- .env file (API keys)
       +-- DevPod + Docker provider --> Docker containers (sandboxes)
```

## What Changes

### 1. Aegra Server

**Production:** Runs in a Docker container on ECS Fargate.
**Local:** Run natively with `aegra serve` or in a Docker container.

Running natively is simpler for development:
```bash
cd /Users/wsmoak/Projects/open-swe-aws-devpod-aegra
cp .env.example .env  # fill in API keys
aegra serve --host 0.0.0.0 --port 2026
```

Running in Docker is closer to production but requires Docker-in-Docker for
the DevPod sandbox containers:
```bash
docker run -v /var/run/docker.sock:/var/run/docker.sock \
  -p 2026:2026 --env-file .env openswe:latest
```

Recommendation: run natively for local dev. DevPod needs to invoke `docker`
commands on the host to create sandbox containers, which is simplest when
Aegra runs directly on the host.

### 2. Sandbox Backend

**Production:** `SANDBOX_TYPE=devpod`, `DEVPOD_PROVIDER=aws` -- creates EC2 instances.
**Local:** `SANDBOX_TYPE=devpod`, `DEVPOD_PROVIDER=docker` -- creates Docker containers.

DevPod natively supports a `docker` provider. The existing code in
`/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/integrations/devpod.py`
calls `_ensure_provider(provider)` which runs `devpod provider add {provider}`.
Setting `DEVPOD_PROVIDER=docker` should work with no code changes.

What the Docker provider does:
- `devpod up <name> --provider docker --source git:<repo-url>` creates a
  Docker container, clones the repo, and sets up the devcontainer
- `devpod ssh <name> --command "..."` executes commands inside the container
- Each workspace is an independent Docker container with its own filesystem

### 3. Multiple Simultaneous Sandboxes

This already works. In `server.py:426`:
```python
SANDBOX_BACKENDS[thread_id] = sandbox_backend
```

Each Aegra thread gets its own sandbox backend. With the Docker provider, each
thread creates a separate Docker container. They don't conflict because:
- Each container has a unique name (generated from thread ID or repo name)
- Each has its own filesystem, network namespace, and process space
- DevPod manages the lifecycle independently per workspace

Resource considerations for running multiple sandboxes on one machine:
- Each devcontainer is a full Docker container (typically 1-4 GB RAM)
- The prebuild images help with startup time but not memory
- Practical limit: 3-5 simultaneous sandboxes on a 32GB Mac, depending on
  what the agent is doing inside them

### 4. Database

**Production:** RDS PostgreSQL.
**Local:** PostgreSQL in Docker.

```bash
docker run -d --name openswe-postgres \
  -p 5432:5432 \
  -e POSTGRES_DB=aegra \
  -e POSTGRES_PASSWORD=localdev \
  -v openswe-pgdata:/var/lib/postgresql/data \
  postgres:16
```

Set in `.env`:
```
DATABASE_URL=postgresql://postgres:localdev@localhost:5432/aegra
```

Aegra's `DatabaseSettings` (in aegra_api/settings.py) defaults to
`localhost:5432/aegra` with `postgres/postgres` credentials, so this
mostly works out of the box.

### 5. Secrets / API Keys

**Production:** AWS Secrets Manager, injected as environment variables by ECS.
**Local:** A `.env` file in the repo root (gitignored).

Required keys:
```
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...
TOKEN_ENCRYPTION_KEY=<32-byte-base64-fernet-key>
```

Optional (for webhook-based interaction, not needed for Open WebUI):
```
GITHUB_APP_ID=...
GITHUB_APP_PRIVATE_KEY=...
GITHUB_APP_INSTALLATION_ID=...
GITHUB_WEBHOOK_SECRET=...
SLACK_BOT_TOKEN=...
SLACK_SIGNING_SECRET=...
```

### 6. Frontend / Interaction

**Production:** GitHub issue comments and Slack messages trigger webhooks.
**Local:** Open WebUI with a pipe function that talks to Aegra's API.

See the separate investigation: `open-webui-integration.md`.

The Aegra server exposes the LangGraph-compatible API:
- `POST /threads` -- create a conversation thread
- `POST /threads/{id}/runs` -- send a message / start an agent run
- `GET /threads/{id}/runs/{run_id}/stream` -- stream the response

The Open WebUI pipe function translates between the OpenAI chat protocol
and this API.

You can also interact directly with the API using the `langgraph-sdk`:
```python
from langgraph_sdk import get_client
client = get_client(url="http://localhost:2026")
thread = await client.threads.create()
run = await client.runs.create(
    thread_id=thread["thread_id"],
    graph_id="agent",
    input={"messages": [{"role": "user", "content": "Add timestamp to README"}]},
    config={"configurable": {"repo": {"owner": "wsmoak", "name": "rails-otel-demo"}}},
)
```

### 7. What Can Be Removed

The following AWS-specific code paths are unused locally:
- `_fetch_fargate_credentials()` in devpod.py -- only runs when
  `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI` is set (Fargate-only)
- `_login_ecr()` -- only needed for prebuild images stored in ECR
- `_ensure_aws_config()` -- only needed for the AWS provider
- The AMI copy workaround in the runbook -- irrelevant for Docker provider
- All Terraform configuration

None of this needs to be deleted; it's all gated on environment variables
and won't activate locally.

## Environment Variable Summary

```bash
# Core
SANDBOX_TYPE=devpod
DEVPOD_PROVIDER=docker
DATABASE_URL=postgresql://postgres:localdev@localhost:5432/aegra
LANGGRAPH_URL=http://localhost:2026
ENV_MODE=LOCAL

# API keys
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...
TOKEN_ENCRYPTION_KEY=<generate with: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# Optional: point to a devcontainer repo for multi-repo setup
DEVPOD_SOURCE_REPO=https://github.com/wsmoak/multi-repo-dev-containers

# Optional: observability (no LangSmith needed)
# OTEL_TARGETS=LANGFUSE
# LANGFUSE_BASE_URL=http://localhost:3000
```

## Docker Compose (Full Local Stack)

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: aegra
      POSTGRES_PASSWORD: localdev
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    ports:
      - "3000:8080"
    environment:
      - WEBUI_AUTH=false  # single-user local dev
    volumes:
      - webui-data:/app/backend/data

volumes:
  pgdata:
  webui-data:
```

Then run Aegra natively on the host (not in Docker) so it can invoke
`devpod` and `docker` commands directly:
```bash
aegra serve --host 0.0.0.0 --port 2026
```

## Vendor Lock-In Status

| Component | License | Lock-in? |
|---|---|---|
| Aegra (aegra-cli + aegra-api) | Apache-2.0 | No |
| deepagents | MIT | No |
| langgraph / langgraph-sdk | MIT | No (but LangChain ecosystem) |
| DevPod | Apache-2.0 | No |
| Open WebUI | MIT | No |
| PostgreSQL | PostgreSQL License | No |
| LangSmith | Proprietary | Not required -- use Langfuse/Phoenix |
| LangGraph Studio | Proprietary | Not required -- use Open WebUI |

The only proprietary component in the current stack is `langchain-anthropic`
which is MIT but calls the Anthropic API (pay-per-use, no lock-in on the
client side).

## Unknowns and Risks

1. **DevPod Docker provider maturity** -- less tested than the AWS provider
   in this codebase. May need debugging around workspace naming, volume
   mounts, and devcontainer feature support.

2. **Prebuild images** -- currently stored in ECR. Locally, you'd either
   skip prebuilds (slower sandbox startup) or push to a local registry.

3. **Resource limits** -- multiple Docker sandboxes on one machine can
   exhaust RAM and CPU. May need to set Docker resource limits per container.

4. **Networking** -- the agent inside a Docker sandbox needs to reach
   GitHub (for git clone/push). Docker's default bridge networking handles
   this, but corporate firewalls or VPNs might interfere.

5. **Aegra database migrations** -- need to run `aegra` migrations against
   the local Postgres before first use. Haven't verified the exact command.

## Next Steps

1. Install DevPod Docker provider locally: `devpod provider add docker`
2. Test manually: `devpod up test-ws --provider docker --source git:https://github.com/wsmoak/rails-otel-demo --ide none`
3. Start local Postgres and run Aegra migrations
4. Start Aegra with `SANDBOX_TYPE=devpod DEVPOD_PROVIDER=docker aegra serve`
5. Test via `langgraph-sdk` Python script before wiring up Open WebUI
6. Set up Open WebUI + pipe function for the chat interface
