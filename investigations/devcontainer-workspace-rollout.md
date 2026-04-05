# Devcontainer Workspace Rollout - Investigation Notes

## Date: 2026-04-05

## What was done

Implemented the plan in `plans/switch-to-devcontainer-workspaces.md` to switch OpenSWE from `--source image:bracelangchain/deepagents-sandbox:v1` to `--source git:https://github.com/{owner}/{repo}` with `--prebuild-repository` for devcontainer-based workspaces.

## Changes made

### Terraform (`/Users/wsmoak/Projects/aws-infrastructure/open-swe`)
- **ecr.tf**: Added `open-swe-devcontainer-prebuilds` ECR repository with lifecycle policy (keep 10 images)
- **iam_ecs.tf**: Added ECR read permissions AND IAM role management permissions (CreateRole, CreateInstanceProfile, PassRole, etc.) to the ECS task role. The IAM permissions were needed because DevPod creates a `devpod-ec2-role` instance profile for EC2 instances.
- **ecs.tf**: Added `DEVPOD_PREBUILD_REPOSITORY` env var pointing to the new ECR repo

### Agent code (`/Users/wsmoak/Projects/open-swe-aws-devpod-aegra`)
- **agent/utils/sandbox.py**: Added `**kwargs` to `create_sandbox()`, forwarded to factory
- **agent/integrations/devpod.py**: Major changes:
  - `create_devpod_sandbox()` now accepts `repo_owner`, `repo_name`, `github_token` kwargs
  - When repo info provided, uses `--source git:` instead of `--source image:`
  - Adds `--prebuild-repository` when `DEVPOD_PREBUILD_REPOSITORY` env var is set
  - `_setup_host_git_credentials()` writes `.git-credentials` on host for DevPod to clone private repos
  - `_login_ecr()` uses boto3 to get ECR auth token and docker login, so credential tunnel forwards valid ECR creds to EC2
  - `DevPodBackend.__init__` takes `git_source` flag
  - `DevPodBackend.get_work_dir()` returns `/workspaces` in git-source mode
  - `DevPodBackend.execute()` uses `--start-services=false` on `devpod ssh` to prevent port-forwarding crashes
  - Increased `DEVPOD_UP_TIMEOUT` from 300s to 600s
- **agent/server.py**: All `create_sandbox()` call sites now pass `repo_owner`, `repo_name`, `github_token`
- **agent/integrations/*.py**: All factory functions accept `**kwargs`
- **pyproject.toml**: Added `boto3>=1.35.0` dependency

### Prebuild image
- Built with fork's devpod binary (`/tmp/devpod-fork`) using `--platform linux/amd64`
- Hash: `devpod-fcd48d664a76c8fe637f28db02ab343b`
- Pushed to `255104623693.dkr.ecr.us-east-2.amazonaws.com/open-swe-devcontainer-prebuilds`

### Local devpod binary
- Replaced `/usr/local/bin/devpod` (was upstream v0.6.15) with fork binary built from `/Users/wsmoak/Projects/devpod`
- Shows `v0.0.0` (dev build, no ldflags). Can rebuild with `-ldflags "-X github.com/skevetter/devpod/pkg/version.version=v0.18.2"` for correct version.

## Issues encountered and fixed

1. **IAM CreateRole permission**: DevPod's AWS provider creates a `devpod-ec2-role` IAM role for EC2 instances. Added IAM management permissions to the ECS task role.

2. **Port forwarding crash**: `devcontainer.json` has `forwardPorts: [3000]`. DevPod's SSH tried to forward this port, failed on Fargate, and crashed the SSH session with exit code 1. The `_is_workspace_unreachable` check caught "use of closed network connection" and raised `SandboxUnavailableError`. Fix: `--start-services=false` on `devpod ssh`.

3. **Prebuild hash mismatch**: Built locally on arm64 Mac, but EC2 runs amd64. The prebuild hash includes architecture, so they didn't match. Fix: `--platform linux/amd64` when building prebuilds.

4. **ECR 401 Unauthorized**: EC2 instance couldn't pull prebuild from ECR. DevPod's credential tunnel forwards docker creds from host, but host (Fargate) wasn't logged into ECR. The `aws` CLI isn't in the Docker image. Fix: added boto3, used it to get ECR auth token and `docker login` before `devpod up`.

## Status as of session end

- All code and terraform changes applied
- Latest agent image deployed to ECS (includes boto3, --start-services=false, ECR login fixes)
- Prebuild image in ECR with correct amd64 hash
- **E2E VERIFIED** -- issue #111 triggered the agent successfully. Prebuild cache hit confirmed in logs: "Found existing prebuilt image ...devpod-fcd48d664a76c8fe637f28db02ab343b". Agent opened PR #112.

## Test issues created
- #107 - failed (IAM CreateRole permission missing)
- #108 - failed (port forwarding crash, old code still deployed)
- #109 - failed (port forwarding crash + ECR 401, fixes not yet deployed)
- #110 - created before latest fixes deployed, failed
- #111 - SUCCESS. Prebuild cache hit, agent completed task, opened PR #112
