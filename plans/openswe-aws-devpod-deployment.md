# Plan: Deploy OpenSWE to AWS with DevPod Sandbox

## Context

OpenSWE's default sandbox (LangSmith) is a paid service. To achieve a fully free, self-hosted AWS deployment, we need to:
1. **Phase A** – Implement a DevPod sandbox provider so the agent can execute code without LangSmith
2. **Phase B** – Add Terraform infrastructure to the existing `aws-infrastructure` project to host the full OpenSWE stack

DevPod (https://devpod.sh) is open-source and has a native AWS provider that spins up EC2 instances as dev environments. Each agent run gets its own isolated DevPod workspace.

**Existing infrastructure** (`/Users/wsmoak/Projects/aws-infrastructure`):
- Region: `us-east-2`, profile: `terraform-admin`
- Only S3 + IAM exist today — no VPC, ECS, ECR, RDS, or networking
- Local Terraform state (terraform.tfstate), no remote backend
- Flat .tf file structure (providers.tf, iam.tf, s3.tf)

---

## Phase A: DevPod Sandbox Provider

### What needs to be built

**New file:** `/Users/wsmoak/Projects/open-swe-with-devpod/agent/integrations/devpod.py`

Implement `SandboxBackendProtocol` using the DevPod CLI. DevPod must be installed in the container that runs the OpenSWE agent.

Required protocol methods (based on the existing protocol):
- `id: str` – workspace name (unique per thread/run)
- `execute(command, timeout)` – `devpod ssh <workspace> --command "<wrapped-command>"`; redirect command stderr to stdout inside the shell so DevPod's own status messages stay separate
- `upload_files(files)` – pipe bytes over stdin: `devpod ssh <workspace> --command "tee <path>"` for each file; **must be implemented — it is abstract in `BaseSandbox`**
- `download_files(paths)` – `devpod ssh <workspace> --command "cat <path>"` for each file; **must be implemented — it is abstract in `BaseSandbox`**
- `write`, `read`, `edit`, `grep_raw`, `glob_info`, `ls_info` – all inherited from `BaseSandbox` and implemented via `execute()`; no override needed

Workspace lifecycle:
- **Create**: `devpod up <workspace-name> --provider aws --ide none --source image:<image>`
- **Reconnect**: if `sandbox_id` is provided, skip creation and SSH directly
- **Delete**: `devpod delete <workspace-name> --force` (call at run end)

Environment variables:
- `DEVPOD_PROVIDER` – provider name (default: `aws`)
- `DEVPOD_WORKSPACE_IMAGE` – base image (default: `bracelangchain/deepagents-sandbox:v1`)
- AWS credentials via IAM role (on ECS) or env vars (local dev)

**File to modify:** `/Users/wsmoak/Projects/open-swe-with-devpod/agent/utils/sandbox.py`
- Add `"devpod": create_devpod_sandbox` to `SANDBOX_FACTORIES`

**File to modify:** `/Users/wsmoak/Projects/open-swe-with-devpod/Dockerfile`
- Add a `RUN` step to install the DevPod CLI binary (download from GitHub releases)

### Key implementation notes

- Workspace name = derived from LangGraph `thread_id` so the same sandbox is reused across tool calls in one run
- Use `subprocess.run()` for all DevPod CLI calls; capture stdout/stderr and map to `ExecuteResponse`
- `execute()` uses `--command` flag (not `--`), wraps the command as `{ <cmd>; } 2>&1` to merge stderr into stdout so DevPod's own status messages on subprocess stderr stay separate
- `upload_files`: `subprocess.run(["devpod", "ssh", name, "--command", f"tee {path}"], input=content)` for each file; collect `FileUploadResponse` per file, catch per-file exceptions
- `download_files`: `subprocess.run(["devpod", "ssh", name, "--command", f"cat {path}"])` for each file; collect `FileDownloadResponse` per file, catch per-file exceptions
- **`upload_files` and `download_files` are abstract in `BaseSandbox`** — omitting them causes `TypeError` on instantiation; they were not implemented in the initial version and must be added
- Pattern the implementation after `daytona.py` for lifecycle (create/reconnect/execute); `langsmith.py` for `upload_files`/`download_files` shape

### Testing Phase A

**Unit tests** (no DevPod required):

- Write `tests/test_devpod.py` using `unittest.mock.patch("subprocess.run")`
- Cover: `execute()` builds the right CLI args and maps stdout/exit_code; workspace creation failure raises `RuntimeError`; reconnect path skips `devpod up`; `upload_files` pipes bytes correctly; `download_files` returns file content

**Integration tests** (docker provider, requires DevPod CLI + Docker Desktop):

1. `devpod provider add docker` (one-time setup)
2. `SANDBOX_TYPE=devpod DEVPOD_PROVIDER=docker python -c "from agent.integrations.devpod import create_devpod_sandbox; sb = create_devpod_sandbox(); ..."`
3. Exercise each method against the live workspace:
   - `execute("echo hello")` → verify output
   - `write("/tmp/test.txt", "hello")` then `read("/tmp/test.txt")` → verify round-trip
   - `edit("/tmp/test.txt", "hello", "world")` → verify replacement
   - `upload_files([("/tmp/upload.txt", b"uploaded")])` → verify no error
   - `download_files(["/tmp/upload.txt"])` → verify content matches
   - `delete()` → verify workspace is removed from `devpod list`
4. Trigger a full agent run via GitHub issue comment to confirm end-to-end flow with a real LangGraph thread_id

**What cannot be tested locally:**

- IAM role credentials for the AWS provider — only testable in ECS

**What requires `make dev` (local LangGraph server) but is otherwise fully testable locally:**

- `_update_thread_sandbox_metadata` — `langgraph dev` starts a local LangGraph server at `http://localhost:2024`; `langgraph_sdk.get_client()` connects there by default; run `make dev` in one terminal and trigger a run from another to confirm thread metadata is written

---

## Phase B: Terraform Infrastructure

### Architecture

```
Internet
  └── ALB (HTTPS, ACM cert)
        └── ECS Fargate Service (OpenSWE / LangGraph container)
              ├── Port 8123 – LangGraph server (agent graph + webhook FastAPI app)
              ├── Reads env vars from Secrets Manager
              ├── Logs to CloudWatch
              └── IAM Task Role
                    ├── EC2 permissions (DevPod creates EC2 instances for sandboxes)
                    └── Secrets Manager read

Single AZ, single private subnet (no multi-AZ, no redundancy — test/blog deployment):
  ├── RDS PostgreSQL – LangGraph state persistence (single-AZ)
  └── ElastiCache Redis – LangGraph run queue (single node)
```

### New Terraform files in `/Users/wsmoak/Projects/aws-infrastructure/open-swe/`

A separate subdirectory with its own `terraform.tfstate`, so `terraform destroy` only affects OpenSWE resources and never touches the existing S3/IAM state in the parent directory.

| File | Resources |
|------|-----------|
| `vpc.tf` | VPC, one public + one private subnet (single AZ), IGW, one NAT gateway, route tables |
| `ecr.tf` | ECR repository for the OpenSWE/LangGraph Docker image |
| `rds.tf` | RDS PostgreSQL instance (LangGraph state), single-AZ, subnet group, security group |
| `elasticache.tf` | ElastiCache Redis single-node cluster (LangGraph queue), subnet group, security group |
| `secrets.tf` | Secrets Manager secret for all OpenSWE env vars |
| `ecs.tf` | ECS cluster, task definition, Fargate service |
| `alb.tf` | ALB, target group, HTTPS listener, ACM certificate |
| `iam_ecs.tf` | ECS task execution role + task role (with EC2 perms for DevPod) |
| `cloudwatch.tf` | Log group for ECS |
| `variables.tf` | Input variables (domain name, instance sizes, secret values) |
| `outputs.tf` | ALB DNS name, ECR repo URL |

### LangGraph self-hosting

`langgraph build -t <ecr-repo-url>:latest` produces the production Docker image. It runs on port 8123 and needs:
- `POSTGRES_URI` – from RDS
- `REDIS_URI` – from ElastiCache
- `SANDBOX_TYPE=devpod`
- `DEVPOD_PROVIDER=aws`
- Secrets from Secrets Manager (see below)

### Secrets Manager contents

All of these go into the `open-swe/env` secret. The ECS task definition injects them at container startup.

| Variable | Source | Required |
|----------|--------|----------|
| `ANTHROPIC_API_KEY` | Anthropic console — the agent uses `anthropic:claude-opus-4-6` as its LLM | Yes |
| `GITHUB_TOKEN` | GitHub PAT or App token — for cloning repos and opening PRs | Yes |
| `SLACK_BOT_TOKEN` | Slack App → OAuth & Permissions (`xoxb-...`) | If using Slack |
| `SLACK_SIGNING_SECRET` | Slack App → Basic Information | If using Slack |
| `SLACK_BOT_USER_ID` | Slack App settings — the bot's user ID | If using Slack |
| `SLACK_BOT_USERNAME` | Display name for the bot | If using Slack |
| `SLACK_REPO_OWNER` | GitHub org/user — default repo for Slack-triggered tasks | If using Slack |
| `SLACK_REPO_NAME` | GitHub repo name — default repo for Slack-triggered tasks | If using Slack |
| `LINEAR_API_KEY` | Linear API settings | If using Linear |
| `LANGSMITH_API_KEY_PROD` | LangSmith console — for tracing/observability | Optional |

**Note:** GitHub-triggered tasks get the repo from the webhook payload, so they work across any repo the GitHub App is installed on. Slack-triggered tasks default to `SLACK_REPO_OWNER/SLACK_REPO_NAME`.

### Domain and TLS

- Domain: `openswe.wendysmoak.com`
- DNS managed in Cloudflare (domain registered elsewhere)
- Set the Cloudflare CNAME to **DNS only (grey cloud)** — not proxied — to avoid TLS conflicts with the ALB

**Steps:**
1. Request ACM cert for `openswe.wendysmoak.com` (via Terraform `aws_acm_certificate`)
2. Add the ACM validation CNAME record in Cloudflare
3. After `terraform apply`, add a CNAME in Cloudflare: `openswe.wendysmoak.com` → ALB DNS name
4. After `terraform destroy`, remove both CNAME records

### Slack Integration

OpenSWE handles Slack via the `/webhooks/slack` endpoint on the FastAPI app. Slack sends HTTP POST events to this URL when the bot is mentioned.

**What's needed:**
1. A Slack App (created at api.slack.com) with:
   - Bot Token Scopes: `app_mentions:read`, `chat:write`, `channels:history`, `groups:history`
   - Event subscriptions enabled, Request URL: `https://openswe.wendysmoak.com/webhooks/slack`
   - Slash commands or mentions configured as desired
2. Env vars in Secrets Manager:
   - `SLACK_BOT_TOKEN` – bot OAuth token (`xoxb-...`)
   - `SLACK_SIGNING_SECRET` – from Slack App's Basic Information page
   - `SLACK_BOT_USER_ID` – the bot's user ID (found in Slack App settings)
   - `SLACK_BOT_USERNAME` – display name
   - `SLACK_REPO_OWNER` / `SLACK_REPO_NAME` – GitHub repo the bot operates on
3. After `terraform apply`, update the Slack App's Request URL to `https://openswe.wendysmoak.com/webhooks/slack`

**Note:** The ALB listener must accept HTTPS (port 443) — Slack requires HTTPS for event subscriptions. The ACM certificate must be valid before Slack will verify the endpoint.

### DevPod + ECS IAM

The ECS task role needs EC2 permissions so DevPod can provision sandbox instances:
```
ec2:RunInstances, ec2:TerminateInstances, ec2:DescribeInstances,
ec2:CreateKeyPair, ec2:DeleteKeyPair, ec2:DescribeKeyPairs,
ec2:CreateSecurityGroup, ec2:DeleteSecurityGroup,
ec2:AuthorizeSecurityGroupIngress, ec2:DescribeSecurityGroups,
ec2:CreateTags, ec2:DescribeSubnets, ec2:DescribeVpcs
```

---

## Order of work

- [x] Phase A: DevPod sandbox provider (open-swe repo)
  - [x] `agent/integrations/devpod.py` — `execute()`, workspace create/reconnect/delete, `_generate_workspace_name`, `_update_thread_sandbox_metadata`
  - [x] Update `agent/utils/sandbox.py`
  - [x] Add DevPod CLI to `Dockerfile`
  - [x] Implement `upload_files` in `DevPodBackend` (abstract method — was missing, caused `TypeError` on instantiation)
  - [x] Implement `download_files` in `DevPodBackend` (same issue)
  - [x] Write `tests/test_devpod.py` unit tests (mock subprocess) — 20 tests, all passing
- [~] Test Phase A (partial — blocked by auth issue with docker provider)
  - [x] Docker workspace creation confirmed via Docker Desktop
  - [N/A] Integration test: write/read/edit/upload/download/delete against docker provider (blocked by auth issue)
  - [N/A] End-to-end agent run via GitHub issue comment (deferred — will test after AWS deployment)
- [x] Phase B: Terraform (aws-infrastructure repo)
  - [x] `vpc.tf`, `ecr.tf`
  - [x] `rds.tf`, `elasticache.tf`
  - [x] `secrets.tf`, `iam_ecs.tf`, `cloudwatch.tf`
  - [x] `ecs.tf`, `alb.tf`
  - [x] `variables.tf`, `outputs.tf`
  - [x] `cd /Users/wsmoak/Projects/aws-infrastructure/open-swe && terraform init && terraform plan && terraform apply`
- [x] `langgraph build` → push to ECR
- [x] Update webhook URLs (GitHub App, Linear, Slack) to ALB DNS name
- [ ] Fix DevPod provider AMI lookup failure
  - [ ] Upgrade DevPod CLI from v0.6.5 to v0.6.15 (in `langgraph.json` dockerfile_lines and/or Dockerfile)
  - [ ] Also check devpod-provider-aws version (currently v0.0.17, downloaded at runtime)
  - [ ] If AMI lookup still broken, fix the provider or pre-seed `~/.devpod/` config
  - [ ] ECS is now X86_64 (changed from ARM64 on 2026-03-22)
  - [ ] Rebuild (`DOCKER_DEFAULT_PLATFORM=linux/amd64 langgraph build`), push, deploy, test webhook
- [ ] Write and publish blog post (see Phase C below)

## Phase D: DevPod with `--source git:` and Devcontainer Support

### Motivation

The current Phase A implementation uses `--source image:<image>`, which creates a generic sandbox from a pre-built Docker image. The agent clones the target repo into this sandbox at runtime. This works for simple tasks (text edits, PRs) but does not provide project-specific dependencies (language runtimes, databases, test frameworks), so the agent cannot build or run tests.

DevPod is designed to work with the **Dev Container spec** — the same `devcontainer.json` used by VS Code and GitHub Codespaces. When given `--source git:<repo-url>`, DevPod clones the repo, reads `.devcontainer/devcontainer.json`, and builds a fully configured environment with all dependencies.

### What changes in devpod.py

The `devpod up` command changes based on a new env var:

| Mode | Env var | `devpod up` command |
|------|---------|-------------------|
| Image (current) | `DEVPOD_SOURCE_IMAGE=bracelangchain/deepagents-sandbox:v1` | `devpod up <name> --provider aws --ide none --source image:<image>` |
| Git (new) | `DEVPOD_SOURCE_REPO=https://github.com/owner/repo` | `devpod up <name> --provider aws --ide none --source git:<repo-url>` |

- If `DEVPOD_SOURCE_REPO` is set, use `--source git:<repo-url>` instead of `--source image:<image>`
- The repo URL can also come from the agent's runtime context (the GitHub webhook payload includes the repo), so the provider could extract it dynamically rather than requiring a static env var
- When using `--source git:`, DevPod handles cloning — the agent should not re-clone the repo into the workspace

### What the target repo needs

A `.devcontainer/devcontainer.json` in the repo. Examples:

**Simple project (e.g. rails-otel-demo — Ruby + sqlite3):**
```json
{
  "name": "rails-otel-demo",
  "image": "mcr.microsoft.com/devcontainers/ruby:3.3",
  "postCreateCommand": "bundle install && bin/rails db:prepare",
  "customizations": {
    "vscode": {
      "extensions": ["shopify.ruby-lsp"]
    }
  }
}
```

**Project with external services (e.g. Postgres + Redis):**
```json
{
  "name": "my-app",
  "dockerComposeFile": "docker-compose.devcontainer.yml",
  "service": "app",
  "workspaceFolder": "/workspaces/${localWorkspaceFolderBasename}",
  "postCreateCommand": "bundle install && bin/rails db:prepare"
}
```
With a `docker-compose.devcontainer.yml` defining `app`, `db` (Postgres), `redis`, etc.

DevPod supports Docker Compose in devcontainer configs, so multi-service environments work the same way they do in VS Code / Codespaces.

### DevPod provider considerations for `--source git:`

- **Docker provider** (local dev/testing): Docker Compose works natively — DevPod creates containers on Docker Desktop
- **AWS provider** (production): DevPod spins up an EC2 instance and runs Docker inside it. Compose-based devcontainers work because Docker is installed on the instance. The instance needs enough resources for all services (app + db + redis, etc.)
- The AWS provider's instance type may need to be configurable per-project if some projects require more resources than others

### Order of work

- [ ] Add `DEVPOD_SOURCE_REPO` support to `devpod.py` — change `devpod up` source arg based on env var
- [ ] Skip the agent's repo-clone step when using `--source git:` (the code is already in the workspace)
- [ ] Add `.devcontainer/devcontainer.json` to rails-otel-demo as a test case
- [ ] Test locally with Docker provider: `DEVPOD_SOURCE_REPO=https://github.com/wsmoak/rails-otel-demo DEVPOD_PROVIDER=docker`
- [ ] Test that the agent can run `bundle exec rspec` inside the workspace
- [ ] Test with AWS provider after the AMI lookup issue (Phase A blocker) is resolved

### Fallback behavior

If `--source git:` is used but the repo has no `devcontainer.json`, DevPod falls back to a default base image (`mcr.microsoft.com/devcontainers/base`). This gives you a generic Linux environment with git and common tools — roughly equivalent to the current `--source image:` approach but without the project-specific sandbox image. The agent could still clone and edit files, but building/testing would likely fail without the right language runtime.

---

## Future Improvements

### Slack Socket Mode (no webhooks)

Currently the Slack integration uses incoming webhooks, which requires a public URL (ALB in production, ngrok in local dev). A better alternative is **Slack Socket Mode**, where the agent opens an outbound WebSocket connection to Slack's event server — no public URL needed.

- Local dev: works without ngrok
- Production: no ALB listener rule needed for Slack events; only the LangGraph server port needs to be exposed
- Implementation: replace the `/webhooks/slack` FastAPI route with a `slack_bolt` `SocketModeHandler` running as a background thread/task

Reference: `~/Projects/openclaw` uses a socket-based Slack integration worth reviewing for patterns.

---

## Phase C: Blog Post

**File:** `/Users/wsmoak/Projects/wsmoak.github.io/_posts/<date>-openswe-aws-devpod.md`

**Format:** Jekyll post (layout: post, space-separated tags in front matter)

**Suggested tags:** `openswe langgraph aws terraform devpod ecs fargate ai agent`

**Outline:**
1. What OpenSWE is and why self-hosting matters (LangSmith sandbox costs)
2. DevPod as the free, open-source sandbox backend — how the provider pattern works
3. The AWS architecture: ECS Fargate + LangGraph self-hosted + RDS + ElastiCache + ALB
4. How it all connects: Slack/GitHub/Linear → ALB → LangGraph → DevPod EC2 workspace
5. What it took in Terraform (key resources, any gotchas)
6. Lessons learned / what's next (DevPod Phase 2 for on-demand EC2 sandboxes)

**Note:** Write the post after the implementation is complete so it reflects the actual experience and any surprises encountered.

## Critical files to modify

**open-swe repo (worktree: `/Users/wsmoak/Projects/open-swe-with-devpod`):**
- `/Users/wsmoak/Projects/open-swe-with-devpod/agent/integrations/devpod.py` (new)
- `/Users/wsmoak/Projects/open-swe-with-devpod/agent/utils/sandbox.py` (add devpod entry)
- `/Users/wsmoak/Projects/open-swe-with-devpod/Dockerfile` (add devpod CLI)

**aws-infrastructure repo:**
- `/Users/wsmoak/Projects/aws-infrastructure/open-swe/` – new .tf files listed above

## Verification

- **Phase A**: Set `SANDBOX_TYPE=devpod DEVPOD_PROVIDER=docker`, trigger a GitHub comment, confirm a Docker-based DevPod workspace is created and the agent executes code inside it
- **Phase B**: Hit `https://openswe.wendysmoak.com/health`; trigger a GitHub webhook; confirm end-to-end run with DevPod workspaces created as EC2 instances in us-east-2
