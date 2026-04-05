# Handoff: Multi-Repo DevContainer

## Context

Read plans/multi-repo-devcontainer-prs.md for the full plan. We're partway through Phase 1.

The OpenSWE agent now uses a dedicated devcontainer repo (`wsmoak/multi-repo-dev-containers`) instead of the webhook's target repo. The devcontainer clones both `rails-otel-demo` and `django-polls-playwright-demo` into `/workspaces/` and sets them up (Postgres, pip install, bundle install).

## What was done this session (2026-04-05)

1. Pushed django-polls-playwright-demo devcontainer commit (53cb631) to origin/main
2. Created and pushed wsmoak/multi-repo-dev-containers with:
   - `.devcontainer/devcontainer.json` — Python 3.12 base, apt-installed Ruby/Node/Postgres
   - `.devcontainer/post-create.sh` — clones both repos, runs per-project setup
   - `.devcontainer/post-start.sh` — restarts Postgres on container start
3. Added `DEVPOD_SOURCE_REPO` support to devpod.py (commit 46077666 in open-swe-aws-devpod-aegra)
   - When `DEVPOD_SOURCE_REPO` env var is set, uses it as `--source git:` URL
   - Derives workspace name from source repo URL, not webhook repo
4. Added `DEVPOD_SOURCE_REPO=https://github.com/wsmoak/multi-repo-dev-containers` to ecs.tf (commit 81fca98 in aws-infrastructure/open-swe)
5. Built, pushed, deployed, and tested — agent responded to issue #118 and opened PR #119

## What needs to happen next

### Immediate (before next test)

1. **Rename local directory**: `~/Projects/devcontainers` -> `~/Projects/multi-repo-dev-containers`
2. **Update CLAUDE.md** in open-swe-aws-devpod-project to reference the new repo name
3. **Clean stale DevPod workspace**: The `rails-otel-demo` workspace from a failed test is still on AWS. Delete it so the next test uses the correct `multi-repo-dev-containers` workspace name. Check with `devpod list` or look at EC2 instances.

### Build the prebuilt devcontainer image

This is the main TODO. Without it, every agent run does a full apt install + git clone + pip install + bundle install from scratch (~3-5 minutes). The prebuild bakes all of that into a Docker image stored in ECR (`open-swe-devcontainer-prebuilds`).

Steps:
1. Build the devcontainer image locally: `devcontainer build --workspace-folder ~/Projects/multi-repo-dev-containers`
2. Tag and push to ECR prebuild repository
3. Test that DevPod uses the prebuilt image (check logs for prebuild cache hit)

TODO:  shouldn't this be part of aws-infrastructure/open-swe terraform ???

### Clean test of workspace name fix

The test that passed (#118) reused a stale workspace named `rails-otel-demo`. After cleaning that up, verify that a fresh run creates a workspace named `multi-repo-dev-containers` with the correct layout:
```
/workspaces/
  multi-repo-dev-containers/       <-- DevPod clones this
  rails-otel-demo/                 <-- cloned by postCreateCommand
  django-polls-playwright-demo/    <-- cloned by postCreateCommand
```

### Then move to Phase 3 (multi-repo PRs)

See plans/multi-repo-devcontainer-prs.md Phase 3 for details:
- `commit_and_open_pr` tool: add optional `repo_owner`/`repo_name` params
- `open_pr_if_needed` middleware: scan workspace for dirty repos, open PR per repo
- System prompt updates for multi-repo awareness
- `DEVPOD_EXTRA_REPOS` env var

## Key files

| File | Repo | Status |
|------|------|--------|
| `.devcontainer/*` | multi-repo-dev-containers | pushed |
| `agent/integrations/devpod.py` | open-swe-aws-devpod-aegra | committed (46077666), deployed |
| `ecs.tf` | aws-infrastructure/open-swe | committed (81fca98), applied |
| `plans/multi-repo-devcontainer-prs.md` | open-swe-aws-devpod-project | reference |

## Known issues

- The `nodejs npm` apt packages pull in ~560 packages / 668MB. Consider using nodesource PPA for a lighter install, or just accept it since the prebuild bakes this once.
- Ubuntu 22.04 base gives old Ruby/Node/Python via apt. The Python 3.12 devcontainer image (Debian Trixie) gives much better versions (Ruby 3.3, Node 20). If we need Ubuntu 22.04 specifically, use deadsnakes PPA for Python 3.12.
- [FIXED] The RUNBOOK.md in aws-infrastructure/open-swe has uncommitted doc updates from a prior session.
