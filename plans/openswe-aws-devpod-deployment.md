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
- `execute(command, timeout)` – `devpod ssh <workspace> -- bash -c "<command>"`
- `write(file_path, content)` – pipe content over stdin via SSH into the workspace
- `download_files(paths)` – `devpod ssh <workspace> -- cat <path>` for each file
- `upload_files(files)` – write bytes over SSH for each file

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
- For file writes, pipe via stdin: `subprocess.run(["devpod", "ssh", name, "--", f"tee {path}"], input=content.encode())`
- Pattern the implementation after `daytona.py` (similar lifecycle: create/reconnect/execute)

### Testing Phase A locally

1. Install DevPod CLI locally, configure `docker` provider
2. Set `SANDBOX_TYPE=devpod`, `DEVPOD_PROVIDER=docker`
3. Run `make dev` + `make run`, trigger a test via GitHub issue comment
4. Confirm workspace is created in Docker and commands execute inside it

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
- All OpenSWE env vars (from Secrets Manager)
- `SANDBOX_TYPE=devpod`
- `DEVPOD_PROVIDER=aws`

### Domain and TLS

- Domain: `open-swe.wendysmoak.com`
- DNS managed in Cloudflare (domain registered elsewhere)
- Set the Cloudflare CNAME to **DNS only (grey cloud)** — not proxied — to avoid TLS conflicts with the ALB

**Steps:**
1. Request ACM cert for `open-swe.wendysmoak.com` (via Terraform `aws_acm_certificate`)
2. Add the ACM validation CNAME record in Cloudflare
3. After `terraform apply`, add a CNAME in Cloudflare: `open-swe.wendysmoak.com` → ALB DNS name
4. After `terraform destroy`, remove both CNAME records

### Slack Integration

OpenSWE handles Slack via the `/webhooks/slack` endpoint on the FastAPI app. Slack sends HTTP POST events to this URL when the bot is mentioned.

**What's needed:**
1. A Slack App (created at api.slack.com) with:
   - Bot Token Scopes: `app_mentions:read`, `chat:write`, `channels:history`, `groups:history`
   - Event subscriptions enabled, Request URL: `https://open-swe.wendysmoak.com/webhooks/slack`
   - Slash commands or mentions configured as desired
2. Env vars in Secrets Manager:
   - `SLACK_BOT_TOKEN` – bot OAuth token (`xoxb-...`)
   - `SLACK_SIGNING_SECRET` – from Slack App's Basic Information page
   - `SLACK_BOT_USER_ID` – the bot's user ID (found in Slack App settings)
   - `SLACK_BOT_USERNAME` – display name
   - `SLACK_REPO_OWNER` / `SLACK_REPO_NAME` – GitHub repo the bot operates on
3. After `terraform apply`, update the Slack App's Request URL to `https://open-swe.wendysmoak.com/webhooks/slack`

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

- [ ] Phase A: DevPod sandbox provider (open-swe repo)
  - [ ] `agent/integrations/devpod.py`
  - [ ] Update `agent/utils/sandbox.py`
  - [ ] Add DevPod CLI to `Dockerfile`
- [ ] Test Phase A locally (docker provider)
- [ ] Phase B: Terraform (aws-infrastructure repo)
  - [ ] `vpc.tf`, `ecr.tf`
  - [ ] `rds.tf`, `elasticache.tf`
  - [ ] `secrets.tf`, `iam_ecs.tf`, `cloudwatch.tf`
  - [ ] `ecs.tf`, `alb.tf`
  - [ ] `variables.tf`, `outputs.tf`
  - [ ] `cd /Users/wsmoak/Projects/aws-infrastructure/open-swe && terraform init && terraform plan && terraform apply`
- [ ] `langgraph build` → push to ECR
- [ ] Update webhook URLs (GitHub App, Linear, Slack) to ALB DNS name
- [ ] Write and publish blog post (see Phase C below)

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
- **Phase B**: Hit `https://open-swe.wendysmoak.com/health`; trigger a GitHub webhook; confirm end-to-end run with DevPod workspaces created as EC2 instances in us-east-2
