# Handoff: Prebuild Image and Workspace Naming Fixes

## Context

Continuing from handoffs/20260405-multi-repo-devcontainer.md. This session completed Phase 1 and Phase 2 of the multi-repo devcontainer plan.

## What was done this session (2026-04-05 afternoon)

1. Created Dockerfile for prebuilt image in multi-repo-dev-containers
   - Discovered DevPod overwrites `/workspaces/` with a volume mount
   - Repos staged at `/opt/prebuilt-repos/`, copied by post-create.sh at start time
   - apt packages, pip install, gem install bundler, bundle install all baked in
   - Prebuilt image pushed to ECR as `open-swe-devcontainer-prebuilds:multi-repo-latest`

2. Fixed workspace naming collision
   - When DEVPOD_SOURCE_REPO is set, all runs generated the same workspace name
   - Reverted to thread-ID-based naming (`openswe-{thread_id}`) for persistence
   - Each GitHub issue / Slack thread gets its own workspace and EC2 instance

3. Added `ec2:StartInstances` and `ec2:StopInstances` to IAM policy (iam_ecs.tf)

4. Fixed bundler permissions — `sudo gem install bundler` and `sudo bundle install` in post-create.sh

5. Tested both repos successfully:
   - django-polls-playwright-demo issue #4 -> PR #5
   - rails-otel-demo issue #120 -> PR #121

6. Cleaned up 9 stale stopped EC2 instances

7. Added build/push documentation to multi-repo-dev-containers README

## Uncommitted changes

- `open-swe-aws-devpod-aegra`: devpod.py workspace naming change (thread-ID instead of UUID suffix)
- `open-swe-aws-devpod-project`: CLAUDE.md path typo fix, new investigations, this handoff

## What needs to happen next

### Commit outstanding changes
- Commit devpod.py change in open-swe-aws-devpod-aegra
- Commit investigation files and CLAUDE.md fix in open-swe-aws-devpod-project

### EC2 instance cleanup
- No automatic policy exists to terminate stopped DevPod instances
- Consider a Lambda + EventBridge rule to terminate instances stopped > N hours
- Could be defined in Terraform in aws-infrastructure/open-swe

### Phase 3: Multi-repo PRs
See plans/multi-repo-devcontainer-prs.md:
- `commit_and_open_pr` tool: add optional `repo_owner`/`repo_name` params
- `open_pr_if_needed` middleware: scan workspace for dirty repos, open PR per repo
- System prompt updates for multi-repo awareness
- `DEVPOD_EXTRA_REPOS` env var

### Slack integration
- Agent should be steerable via Slack threads
- Workspace persists across interactions in the same thread (that's why we use thread-ID naming)

## Key files changed

| File | Repo | Status |
|------|------|--------|
| `.devcontainer/Dockerfile` | multi-repo-dev-containers | pushed (773f296) |
| `.devcontainer/post-create.sh` | multi-repo-dev-containers | pushed (773f296) |
| `.devcontainer/devcontainer.json` | multi-repo-dev-containers | pushed (773f296) |
| `README.md` | multi-repo-dev-containers | pushed (773f296) |
| `agent/integrations/devpod.py` | open-swe-aws-devpod-aegra | uncommitted |
| `iam_ecs.tf` | aws-infrastructure/open-swe | applied (terraform) |

## Key learnings

1. DevPod manages `/workspaces/` as a volume — anything there in the Dockerfile is lost
2. Workspace names must be unique per thread, not per repo
3. apt bundler version lags; must `gem install bundler` for current version
4. System gem path is root-owned; post-create.sh runs as vscode user, needs sudo
