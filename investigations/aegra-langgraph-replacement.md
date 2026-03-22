# Investigation: Aegra as LangGraph Platform Replacement

## Context

LangGraph Platform requires a LangSmith API key even when self-hosted — it's used for license validation. This means the deployment isn't fully vendor-free. [Aegra](https://github.com/ibbybuilds/aegra) is an open-source (Apache 2.0) drop-in replacement for LangGraph Platform that removes this dependency.

## What Aegra Is

Aegra is a self-hosted alternative to LangSmith Deployments. It implements the same Agent Protocol API, uses the same LangGraph SDK, and supports the same `langgraph.json` config format. It replaces LangGraph Platform's managed infrastructure with FastAPI + PostgreSQL.

- 709 GitHub stars (as of 2026-03-22)
- Python 3.12+, FastAPI, PostgreSQL (with pgvector for semantic store)
- Default port: 2026 (LangGraph Platform uses 8000 in our deployment)
- No Redis required — PostgreSQL handles both persistence and run queue
- Apache 2.0 license

## Compatibility with OpenSWE

### What works unchanged

| Area | Status | Notes |
|------|--------|-------|
| `langgraph.json` config | Compatible | Aegra falls back to `langgraph.json` if `aegra.json` not found |
| `"http": {"app": "agent.webapp:app"}` | Compatible | Same mounting mechanism for custom FastAPI apps |
| LangGraph SDK (`get_client()`, `threads`, `runs.stream()`) | Compatible | Same SDK, same methods |
| Agent graph (`agent.server:get_agent`) | Compatible | LangGraph graphs run identically |
| Custom webhook routes (`/webhooks/github`, `/webhooks/slack`, `/webhooks/linear`) | Compatible | They're just FastAPI routes in the custom app — no platform integration needed |

### What changes

| Area | Change needed | Effort |
|------|---------------|--------|
| Port | Update `LANGGRAPH_URL` env var (2026 instead of 8000) | Trivial |
| Database | PostgreSQL only — can drop ElastiCache Redis | Simplifies infra |
| Docker image | No longer use `langgraph build` — use Aegra's Dockerfile or `aegra up` | Moderate |
| LangSmith API key | No longer needed | The whole point |
| Observability | OpenTelemetry fan-out instead of LangSmith-only tracing | Optional improvement |

### Key finding: no Redis

Aegra uses PostgreSQL for both state persistence and the run queue. This means the ElastiCache Redis cluster (~$9/month) can be eliminated entirely.

### Key finding: dockerfile_lines

OpenSWE uses `langgraph.json`'s `dockerfile_lines` to install the DevPod CLI binary into the LangGraph Platform container. Aegra's config supports the same field (backward compat with `langgraph.json`), but the Docker build process is different — Aegra doesn't have `langgraph build`. The DevPod CLI installation would need to move to a custom Dockerfile.

### Key finding: webhook support

Aegra's feature matrix lists webhook callbacks as "Not yet planned." However, this doesn't affect OpenSWE because OpenSWE's webhooks are self-contained FastAPI routes in `agent/webapp.py`. They don't depend on any LangGraph Platform webhook infrastructure — they just receive HTTP POSTs, verify signatures, and create runs via the LangGraph SDK. Since Aegra mounts the custom app the same way (via `http.app` config), these routes work as-is.

## Infrastructure Impact

### Current (LangGraph Platform)
- ECS Fargate (LangGraph container)
- RDS PostgreSQL (state)
- ElastiCache Redis (run queue)
- LangSmith API key required

### With Aegra
- ECS Fargate (Aegra container)
- RDS PostgreSQL (state + run queue)
- No Redis
- No LangSmith dependency

Estimated savings: ~$9/month (Redis) + removes vendor dependency.

## Migration Steps

1. Create a Dockerfile that installs Aegra, the OpenSWE agent code, and the DevPod CLI
2. Add `aegra.json` (or keep `langgraph.json` — both work) pointing to the agent graph and webapp
3. Update ECS task definition: remove Redis-related env vars, update port, remove `LANGSMITH_API_KEY`
4. Remove ElastiCache Redis from Terraform
5. Update `LANGGRAPH_URL` to point to port 2026
6. Test end-to-end

## Risks

- Aegra is younger and less battle-tested than LangGraph Platform
- No built-in cron/scheduled jobs (not currently used by OpenSWE)
- No RemoteGraph support (not currently used by OpenSWE)
- Community-maintained — smaller team than LangChain

## Verdict

Aegra is a viable replacement. The migration is operationally moderate (Docker image build, Terraform changes) but code changes are minimal. The main benefit is eliminating the LangSmith dependency and simplifying infrastructure (no Redis). Worth pursuing for Phase E or whenever the LangSmith key becomes a friction point.
