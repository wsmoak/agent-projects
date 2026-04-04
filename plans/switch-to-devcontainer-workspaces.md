# Plan: Switch OpenSWE from Generic Sandbox Image to Devcontainer-Based Workspaces

## Context

OpenSWE currently uses `devpod up --source image:bracelangchain/deepagents-sandbox:v1` to spin up a generic sandbox container, then clones the target repo into it. This means the workspace has generic tooling (Python, Go, Node) but not the project-specific environment.

The goal is to switch to devcontainer-based workspaces where:
1. Each target repo has a `.devcontainer/devcontainer.json` defining its environment
2. Devcontainer images are **pre-built** and stored in ECR so workspace creation is fast (pull, not build)
3. DevPod clones the repo as part of `devpod up`, so the subsequent clone step becomes a pull/checkout

## Approach

Use `devpod up --source git:https://github.com/{owner}/{repo} --prebuild-repository <ecr-url>` instead of `--source image:`. DevPod will:
- Clone the repo
- Find `.devcontainer/devcontainer.json`
- Look for a matching pre-built image in ECR (by config hash)
- If found, pull it (fast); if not, build from scratch (slow fallback)
- Start the container with the repo mounted at `/workspaces/{repo-name}`

## Changes

### 1. Terraform: Add ECR repo for devcontainer prebuilds

**File:** `/Users/wsmoak/Projects/aws-infrastructure/open-swe/ecr.tf`

Add a second ECR repository `open-swe-devcontainer-prebuilds` alongside the existing `open-swe` repo.

### 2. Terraform: Add ECR pull permissions to task role

**File:** `/Users/wsmoak/Projects/aws-infrastructure/open-swe/iam_ecs.tf`

The `ecs_task_devpod` policy (line 61) currently only has EC2 permissions. Add ECR read permissions so the DevPod-created EC2 instances can pull pre-built images:
- `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage`, `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`

### 3. Terraform: Add env var to ECS task definition

**File:** `/Users/wsmoak/Projects/aws-infrastructure/open-swe/ecs.tf`

Add to the `environment` block (around line 65):
```
{ name = "DEVPOD_PREBUILD_REPOSITORY", value = "${ecr_repo_url}" }
```

### 4. Code: Pass repo info through sandbox factory

**File:** `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/utils/sandbox.py`

- Change `create_sandbox(sandbox_id=None)` to `create_sandbox(sandbox_id=None, **kwargs)`
- Forward `**kwargs` to the factory function: `factory(sandbox_id, **kwargs)`

### 5. Code: Update `create_devpod_sandbox()` for git source mode

**File:** `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/integrations/devpod.py`

- Update signature to accept `repo_owner`, `repo_name`, `github_token` as keyword args
- When repo info is provided, use `--source git:https://github.com/{owner}/{repo}` instead of `--source image:{image}`
- When `DEVPOD_PREBUILD_REPOSITORY` is set, add `--prebuild-repository` flag
- Set up host-level git credentials (`.git-credentials` file using the GitHub token) so DevPod can clone private repos
- Skip `_disable_git_credential_injection()` in git-source mode (DevPod needs credentials for the clone)
- Keep `--source image:` as fallback when repo info is not available
- Add `get_work_dir()` method to `DevPodBackend` that returns `/workspaces` when in git-source mode, so path resolution works correctly
- Set `_git_source` flag on `DevPodBackend` to track the mode

### 6. Code: Pass repo info from server.py to create_sandbox()

**File:** `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/server.py`

Update all `create_sandbox()` call sites (lines 216, 318, 356) to pass `repo_owner`, `repo_name`, and `github_token` as kwargs. Line 345 (reconnect) stays unchanged.

### 7. Code: Add `**kwargs` to other sandbox factory signatures

Files in `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/integrations/`:
- `daytona.py`, `langsmith.py`, `modal.py`, `runloop.py`, `local.py`
- Add `**kwargs` to their `create_*_sandbox()` functions so they accept and ignore the new args

### 8. Pre-build the devcontainer image

Run manually (or set up CI later):
```bash
# Auth to ECR
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin <ecr-url>

# Pre-build and push
devpod build github.com/wsmoak/rails-otel-demo \
  --repository <ecr-url>/open-swe-devcontainer-prebuilds
```

### 9. (Optional) Update rails-otel-demo devcontainer.json

**File:** `/Users/wsmoak/Projects/rails-otel-demo/.devcontainer/devcontainer.json`

Add prebuild repository to customizations so it's self-documenting:
```json
"customizations": {
  "devpod": {
    "prebuildRepository": ["<ecr-url>/open-swe-devcontainer-prebuilds"]
  }
}
```

## Path resolution detail

With `--source git:`, DevPod sets the working directory inside the container to `/workspaces/{repo-name}`. The existing `resolve_sandbox_work_dir()` in `sandbox_paths.py` (line 34) calls `get_work_dir()` on the backend first (line 63), then falls back to `pwd`. By adding `get_work_dir()` returning `/workspaces` to `DevPodBackend` in git-source mode, `resolve_repo_dir()` correctly computes `/workspaces/{repo-name}`.

The existing `_clone_or_pull_repo_in_sandbox()` (server.py line 68) already handles the "repo exists" case -- it calls `is_valid_git_repo()`, finds the repo, and does a `git pull` instead of cloning. No changes needed there.

## Sequencing

1. Terraform changes (steps 1-3) -- apply first
2. Pre-build the image (step 8) -- needs the ECR repo from step 1
3. Code changes (steps 4-7) -- can be developed in parallel with above
4. Build and deploy the updated agent image
5. Test end-to-end

## Verification

1. After terraform apply: confirm new ECR repo exists and IAM policy is updated
2. After pre-build: confirm image appears in ECR with a `devpod-*` hash tag
3. After deploy: open a new GitHub issue in rails-otel-demo and tag @openswe or @open-swe in the description
4. Check CloudWatch logs for:
   - `devpod up` using `--source git:` and `--prebuild-repository`
   - Repo detected as already cloned (pull, not clone)
   - Agent operating in the correct working directory
5. Verify the agent can successfully complete a task (e.g., edit README.md)

## Risks

- **Git auth on Fargate**: DevPod needs to clone on the host (Fargate container). Writing `.git-credentials` before `devpod up` should handle this. If it fails, logs will show the git clone error.
- **Prebuild cache miss**: If no pre-built image matches, DevPod builds from scratch (~5-10 min for Ruby + Node). The 300s timeout should be increased to 600s.
- **postCreateCommand**: `./bin/setup` runs on every new workspace. This is expected devcontainer behavior and is fine.
