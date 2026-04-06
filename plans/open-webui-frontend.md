# Plan: Open WebUI Frontend for OpenSWE

## Context

OpenSWE currently has two interaction surfaces: GitHub issue comments and Slack. Both are fire-and-forget -- you post a message, the agent works, and eventually comments back or opens a PR. There is no way to watch the agent work in real time or have a conversational back-and-forth.

Open WebUI is an MIT-licensed, self-hosted chat interface (SvelteKit + FastAPI) that can connect to any OpenAI-compatible backend. The goal is to deploy it alongside Aegra on AWS as a chat frontend for OpenSWE, enabling real-time streaming of agent responses.

Investigation completed: `investigations/open-webui-integration.md`

## Architecture

```
                Internet
                   |
              ALB (HTTPS)
             /          \
  chat.wendysmoak.com    openswe.wendysmoak.com
           |                      |
     Open WebUI (ECS)       Aegra (ECS)
           |                      |
           +--- pipe function --->+
           |                      |
     PostgreSQL (RDS)        PostgreSQL (RDS)
     (openwebui DB)          (aegra DB)
```

Both services share the existing ALB via host-based listener rules and the existing RDS instance (separate databases). Open WebUI connects to Aegra via a LangGraph pipe function that translates chat messages into thread/run API calls.

## Prerequisites

- DNS: ability to add `chat.wendysmoak.com` CNAME pointing to the existing ALB
- ACM: certificate covering `chat.wendysmoak.com` (or wildcard `*.wendysmoak.com`)
- The existing Aegra deployment is working

## Steps

### Phase 1: Local Proof of Concept

**Goal:** Verify Open WebUI works with Aegra locally before touching AWS.

1. Run Open WebUI locally with Docker Compose (image `ghcr.io/open-webui/open-webui:main`, port 3000:8080, SQLite is fine for local)
2. Write a minimal pipe function that:
   - Maps Open WebUI chat ID to an Aegra thread ID
   - Sends user messages to `POST /threads/{id}/runs` on local Aegra
   - Streams the agent response back
   - Passes `configurable.repo` (hardcode `wsmoak/rails-otel-demo` initially)
3. Install the pipe in Open WebUI's admin UI and test a conversation
4. Verify streaming works -- messages should appear incrementally

### Phase 2: Terraform Infrastructure

**Goal:** Add Open WebUI to the existing AWS deployment.

#### 2a. ACM Certificate

- Add `chat.wendysmoak.com` to the ACM certificate (or create a separate one)
- Add DNS validation record

#### 2b. ECR Repository

- Add `aws_ecr_repository.openwebui` for the Open WebUI image
- Push `ghcr.io/open-webui/open-webui:main` to ECR (or reference GHCR directly)

#### 2c. RDS: Create Second Database

- Create the `openwebui` database on the existing RDS instance
- Can be done with a `postgresql_database` resource (hashicorp/postgresql provider) or manually via psql

#### 2d. Secrets Manager

- Add `WEBUI_SECRET_KEY` to Secrets Manager (generate a random 32-char key)
- Add `WEBUI_ADMIN_EMAIL` and `WEBUI_ADMIN_PASSWORD` (pre-seeds admin on first boot, avoids race condition with open signup)

#### 2e. ECS Task Definition + Service

New task definition `open-webui` in the existing ECS cluster:
- Image: from ECR or GHCR
- Port: 8080
- Environment:
  - `DATABASE_URL=postgresql://<user>:<pass>@<rds-host>:5432/openwebui`
  - `WEBUI_AUTH=true`
  - `ENABLE_SIGNUP=false` (prevent open registration)
  - `WEBUI_ADMIN_EMAIL` (from Secrets Manager -- pre-seeds admin account on first boot)
  - `WEBUI_ADMIN_PASSWORD` (from Secrets Manager)
  - `WEBUI_SECRET_KEY` (from Secrets Manager)
  - `ENABLE_WEBSOCKET_SUPPORT=false` (single replica, no Redis needed initially)
- CPU/Memory: 512/1024 (Open WebUI is lightweight)
- Same VPC, private subnets, same security group pattern as Aegra

New ECS service `open-webui`:
- Desired count: 1
- Target group: new `open-webui` target group (port 8080, health check `/health`)

#### 2f. ALB: Host-Based Routing

- Add a new target group for Open WebUI (port 8080)
- Change the existing HTTPS listener default action to a fixed 404 response
- Add two listener rules:
  1. Host `openswe.wendysmoak.com` -> Aegra target group (priority 10)
  2. Host `chat.wendysmoak.com` -> Open WebUI target group (priority 20)
- Add the `chat.wendysmoak.com` certificate to the listener (if separate from wildcard)

#### 2g. Security Groups

- ALB SG: consider restricting `chat.wendysmoak.com` ingress to Wendy's IP range instead of `0.0.0.0/0` (single-user setup, no reason to expose to the whole internet). Aegra's webhook endpoints still need `0.0.0.0/0` for GitHub/Slack callbacks, so this may require a separate ALB SG or WAF rule for host-based IP filtering.
- Open WebUI ECS SG: allow inbound 8080 from ALB SG
- RDS SG: add ingress from Open WebUI ECS SG on port 5432
- Open WebUI needs outbound to Aegra on port 2026 (same VPC, covered by existing egress rules)

#### 2h. DNS

- Add Route 53 CNAME: `chat.wendysmoak.com` -> ALB DNS name

### Phase 3: Deploy and Configure

1. Build/push Open WebUI image to ECR (or configure ECS to pull from GHCR)
2. `terraform apply`
3. Verify Open WebUI loads at `https://chat.wendysmoak.com`
4. Log in with the admin credentials from Secrets Manager (account is pre-seeded, no open registration)
5. Install the pipe function (from Phase 1, adjusted for internal Aegra URL: `http://aegra-service:2026` or the private DNS name)
6. Test a conversation that triggers an agent run

### Phase 4: Pipe Function Refinements

1. **Repo selection:** Create one "model" per target repo in Open WebUI (e.g., "OpenSWE - rails-otel-demo", "OpenSWE - django-polls"). Each model uses the same pipe but with a different `configurable.repo` value.
2. **Thread persistence:** Map Open WebUI chat IDs to Aegra thread IDs so conversations persist across page reloads.
3. **Status display:** Surface sandbox creation progress, tool calls, and other agent events in the chat stream.
4. **GitHub identity:** Optionally pass the GitHub username from Open WebUI user metadata to the agent for PR attribution.

## Open Decisions

1. **Image source:** Pull from GHCR directly (simpler) or mirror to ECR (more control, avoids GHCR rate limits)?
2. **Pipe vs. OpenAI wrapper:** The investigation identified three approaches. Pipe function is recommended because it keeps all custom code in Open WebUI (a single Python file) and doesn't modify the agent codebase.
3. **Redis:** Not needed for single replica. Add later if scaling to multiple replicas.
4. **Cost estimate:** ~$15-20/month additional (256 CPU / 512 MiB Fargate task + negligible RDS/ALB incremental cost).

## Files to Create/Modify

- `/Users/wsmoak/Projects/aws-infrastructure/open-swe/alb.tf` -- listener rules, target group
- `/Users/wsmoak/Projects/aws-infrastructure/open-swe/ecs.tf` -- or new `ecs-openwebui.tf` for task def + service
- `/Users/wsmoak/Projects/aws-infrastructure/open-swe/rds.tf` -- RDS SG ingress rule
- `/Users/wsmoak/Projects/aws-infrastructure/open-swe/secrets.tf` -- WEBUI_SECRET_KEY
- `/Users/wsmoak/Projects/aws-infrastructure/open-swe/variables.tf` -- new variables
- `/Users/wsmoak/Projects/aws-infrastructure/open-swe/ecr.tf` -- new repository (if mirroring)
- New pipe function Python file (lives in this project repo or installed via Open WebUI admin UI)
