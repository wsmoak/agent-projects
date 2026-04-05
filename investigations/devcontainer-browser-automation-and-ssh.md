# DevContainer Architecture: Browser Automation

## Current Setup (Image-based)

When OpenSWE handles a GitHub issue today:

1. ECS task runs the OpenSWE agent (LangGraph/Python)
2. The agent calls `devpod up --source image:bracelangchain/deepagents-sandbox:v1`
3. DevPod's AWS provider provisions an EC2 instance (Ubuntu 22.04), installs Docker on it
4. DevPod pulls the sandbox image and runs it as a Docker container on that EC2 instance
5. The agent communicates with the container via `devpod ssh <workspace-name>`

Docker Engine (not Docker Desktop) is used on the EC2 instance. Docker Engine is open source (Apache 2.0) and free for commercial use. Only Docker Desktop requires a paid subscription for larger companies.

## Switching to `--source git:` with Devcontainers

When we switch to `--source git:<repo-url>`, DevPod clones the repo, reads its
`.devcontainer/devcontainer.json`, and runs that image instead of the hardcoded sandbox image.

The EC2 instance and Docker remain the same. What changes is which Docker image runs:
- **Before:** `bracelangchain/deepagents-sandbox:v1` (hardcoded in devpod.py)
- **After:** whatever image is in the devcontainer.json (e.g., `mcr.microsoft.com/devcontainers/ruby:3.3`)

The deepagents tooling does not need to be in the devcontainer image. The agent communicates
with the container via SSH — it does not run agent code inside the container.

The devcontainer's `postCreateCommand` clones the application repo(s) and installs dependencies.
The devcontainer's `postStartCommand` (or `post_start.sh`) starts the application automatically
when the workspace comes up, so by the time the agent begins work, the app is already running.

## Browser Automation

For an agent that makes code changes and then verifies them by clicking around the running app:

- The app runs inside the devcontainer on the EC2 instance
- `PlayWrightBrowserToolkit` from `langchain_community` runs in-process (in the ECS container)
  and cannot reach localhost inside the devcontainer — so it does not apply here
- The right approach is to run playwright **inside the devcontainer** via `execute_bash`

### How it works

1. Agent makes code changes (already works)
2. App is already running (devcontainer `postStartCommand` handles this)
3. Agent writes and executes a playwright script via `execute_bash` against localhost
4. Agent downloads the screenshot file via `sandbox_backend.download_files`
5. Screenshot is passed to the LLM as an image content block (multimodal.py already handles this)
6. LLM inspects the screenshot and decides if the change worked

No new Python dependencies needed in the ECS image. The playwright CLI and skills for this
pattern already exist. Playwright + Chromium needs to be installed in the devcontainer image
(either baked in or via `postCreateCommand`). Playwright runs in headless mode — no display
required.

## Remote SSH Access to Agent-Created Workspaces

When OpenSWE creates a DevPod workspace from ECS, the workspace metadata (SSH keys, EC2
instance address, workspace config) is stored in `~/.devpod/` on the ECS container's ephemeral
filesystem — not on a developer's laptop. The local DevPod installation has no knowledge of
that workspace.

**DevPod Pro** solves this with a central server, but it is a paid product.

### DIY Approach

The current architecture is a reasonable foundation. The pieces to add:

1. **Registry** — DynamoDB (or similar) mapping workspace name → EC2 IP + SSH public key,
   written when `devpod up` completes
2. **Key storage** — SSH keys stored in SSM Parameter Store or Secrets Manager so any client
   can retrieve them
3. **Client tooling** — a small CLI or VS Code extension that queries the registry and
   populates `~/.ssh/config` on demand
4. **Network** — workspaces are on Tailscale VPN, so no public IP exposure needed; no bastion
   required, just Tailscale connectivity

The agent already stores the workspace name (`sandbox_id`) in LangGraph thread metadata
(server.py line 325). The workspace name and EC2 IP could be posted as a GitHub comment when
the workspace is created, giving a developer an immediate pointer to the running environment.

### Current Workaround

To SSH into an agent-created workspace today:
1. Find the EC2 instance IP in the AWS console (filter by creation time / tags)
2. Retrieve the SSH key DevPod stored in `~/.devpod/` on the ECS container (before it stops)
3. Adjust the EC2 security group to allow inbound SSH from your IP (or use Tailscale)
