# Investigation: Open WebUI Integration

**Date:** 2026-04-06
**Status:** Implemented and tested locally. See runbook: `runbooks/local-openswe-setup.md`

## Summary

Open WebUI is an open-source (MIT), self-hosted AI chat interface. It provides a
ChatGPT-like UI that can connect to any OpenAI-compatible API backend. The goal is
to use it as a frontend for OpenSWE, replacing the current GitHub-issue-only and
Slack-only interaction model with a general-purpose chat interface.

## What Open WebUI Is

- MIT-licensed, fully self-hostable, works offline
- SvelteKit frontend + FastAPI backend
- Supports multiple LLM backends via OpenAI-compatible API protocol
- Built-in user auth, multi-user with roles (admin/user)
- Supports RAG (retrieval-augmented generation) out of the box
- Has a "Pipe" / "Function" plugin system for custom backends
- Active project: https://github.com/open-webui/open-webui

## Architecture for Production (AWS)

### Minimal deployment

```
Open WebUI (ECS or EC2)
  |
  +-- PostgreSQL (can share RDS with Aegra, or separate DB)
  +-- Redis (ElastiCache or container sidecar, needed for WebSocket support)
  +-- Connects to Aegra via pipe function
```

### Docker Compose (single-host)

```yaml
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    ports:
      - "3000:8080"
    environment:
      - DATABASE_URL=postgresql://postgres:password@db:5432/openwebui
      - REDIS_URL=redis://redis:6379/0
      - ENABLE_WEBSOCKET_SUPPORT=true
      - WEBSOCKET_MANAGER=redis
      - WEBUI_AUTH=true
      - WEBUI_SECRET_KEY=<generate-one>
    volumes:
      - open-webui-data:/app/backend/data

  db:
    image: postgres:16
    environment:
      POSTGRES_DB: openwebui
      POSTGRES_PASSWORD: password
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redisdata:/data
```

### Scaling notes

- Open WebUI is stateless -- can run multiple replicas behind a load balancer
- Requires PostgreSQL (not SQLite) for multi-replica
- Requires Redis for WebSocket fan-out across replicas
- Default vector DB (ChromaDB) needs to be external for multi-replica
- `WEBUI_SECRET_KEY` must be identical across all replicas

## Connecting Open WebUI to Aegra/OpenSWE

Open WebUI does not natively speak the LangGraph thread/run protocol. Three
approaches exist to bridge the gap:

### Option 1: Pipe Function (recommended)

Open WebUI has a "Function" plugin system. A Pipe function is a Python class that
runs inside Open WebUI and routes messages to a custom backend. Multiple community
examples exist for LangGraph integration.

The pipe would:
1. Extract user message and chat metadata from the Open WebUI request
2. Create or reuse an Aegra thread (mapped from Open WebUI's chat ID)
3. Call `POST /threads/{id}/runs` on the Aegra server
4. Stream the agent's response back to the Open WebUI chat

Reference implementations:
- https://github.com/open-webui/pipelines/tree/main/examples/pipelines/integrations/langgraph_pipeline (official Open WebUI pipelines repo example)
- https://github.com/sieveLau/openwebui-langgraph (standalone example: LangGraph ReAct agent served via Open WebUI pipeline)
- https://github.com/open-webui/open-webui/discussions/13945 (remote LangGraph with state persistence)
- https://github.com/open-webui/open-webui/discussions/17337 (pipe for LangGraph with human-in-the-loop)
- https://pessini.medium.com/from-open-webui-to-langgraph-building-a-human-in-the-loop-pipe-for-real-time-ai-control-26561cca9f9c
- https://medium.com/@davit_martirosyan/integrating-langgraph-agents-into-open-webui-3533cc3a47e1

Key consideration: the pipe needs to pass `configurable.repo` to Aegra so the
agent knows which repo to work on. This could come from:
- A per-model configuration in Open WebUI (each "model" = a repo target)
- User input in the first message ("work on wsmoak/rails-otel-demo")
- A fixed default in the pipe config

### Option 2: OpenAI-compatible wrapper route in webapp.py

Add a `/v1/chat/completions` endpoint directly to the agent's FastAPI app that
translates OpenAI chat protocol into Aegra's thread/run calls. Open WebUI would
see it as a standard OpenAI connection.

Pros: no pipe code to maintain in Open WebUI
Cons: adds complexity to the agent codebase, tighter coupling

### Option 3: LiteLLM proxy

Use LiteLLM as a middleware between Open WebUI and Aegra. LiteLLM provides
OpenAI-compatible endpoints and can proxy to custom backends.

Pros: proven infrastructure, handles auth and rate limiting
Cons: another service to run, may not map cleanly to the thread/run model

## AWS Deployment Considerations

### Where to run it

- **ECS Fargate** (like Aegra): simple, consistent with existing infra
- **EC2**: if you want to colocate with other services
- Same VPC as Aegra so the pipe can reach it at a private DNS name

### Networking

- Open WebUI needs to be reachable from the internet (HTTPS via ALB)
- Open WebUI needs to reach Aegra's API (internal, same VPC)
- Could share the existing ALB with a separate target group and host-based routing
  (e.g., `chat.wendysmoak.com` -> Open WebUI, `openswe.wendysmoak.com` -> Aegra)

### Database

- Can share the existing RDS instance with a separate database
  (`CREATE DATABASE openwebui` alongside the existing `aegra` database)
- Or use a dedicated RDS instance if isolation is preferred

### Secrets

- `WEBUI_SECRET_KEY` -- generate and store in Secrets Manager
- No LLM API keys needed in Open WebUI itself (Aegra handles those)

## Vendor Lock-In Assessment

- Open WebUI: MIT, no lock-in
- Aegra: Apache-2.0, no lock-in (self-hosted LangGraph alternative)
- The pipe function is the only custom glue code -- it's a single Python file
- No LangSmith account or API key required for any of this
- Observability can go to Langfuse or Phoenix (both open source) instead of LangSmith

## Open Questions

1. How to handle repo selection in the UI -- per-model config vs. user input?
2. Should Open WebUI users map to GitHub identities for PR attribution?
3. Do we want the webhooks (GitHub/Slack) to continue working alongside Open WebUI,
   or migrate fully?
4. Should Open WebUI share the ALB or get its own?
5. Human-in-the-loop: the pipe examples support LangGraph interrupts -- do we need
   that for OpenSWE's workflow?

## Next Steps

1. Run Open WebUI locally with Docker to evaluate the UI
2. Write a minimal pipe function that talks to a local Aegra instance
3. Test with a real agent run (create sandbox, clone repo, do work)
4. Plan the Terraform additions for AWS deployment
