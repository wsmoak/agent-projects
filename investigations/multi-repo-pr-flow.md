# Investigation: How the OpenSWE Agent Handles Repo Identity and PR Creation

Date: 2026-04-05

## Question

When the GitHub App has access to multiple repos (rails-otel-demo and django-polls-playwright-demo), and the agent uses a multi-repo devcontainer workspace, what determines which repo the agent opens a PR against? What needs to change for multi-repo PR support?

## Findings

### Webhook -> Thread -> PR Flow

The repo identity flows through three stages:

1. **Webhook receipt** (`agent/webapp.py:1453-1457`): Extracts `owner` and `name` from the webhook payload's `repository` field. This is whichever repo the issue was opened in.

2. **Thread configuration** (`agent/webapp.py:1399-1411`): The repo config is stored in the LangGraph thread's `configurable` dict under the `repo` key. This is set once when the thread is created and never changes.

3. **PR creation** (`agent/tools/commit_and_open_pr.py:119-127`): Reads `repo` from the thread configurable. Uses `repo_owner` and `repo_name` to determine where to push and create the PR.

### DevPod Source vs Webhook Repo

These are independent:

- `DEVPOD_SOURCE_REPO` env var (`agent/integrations/devpod.py:306`) controls what DevPod clones as the workspace. When set to the multi-repo-dev-containers URL, the workspace gets all repos via postCreateCommand.
- The **webhook repo** controls PR targeting. These don't interact.

### resolve_repo_dir

`agent/utils/sandbox_paths.py:20-26` resolves where the repo lives inside the sandbox. When using the devpod backend, it looks for `/workspaces/{repo_name}`. Since our multi-repo layout puts repos at `/workspaces/rails-otel-demo` and `/workspaces/django-polls-playwright-demo`, this should resolve correctly for whichever repo triggered the webhook.

### Server-side repo clone

`agent/server.py:332-336` calls `_clone_or_pull_repo_in_sandbox()` which clones the webhook repo into the sandbox. With the multi-repo devcontainer, the repos are already cloned by postCreateCommand. The clone-or-pull function should detect the existing clone and pull instead of re-cloning. Need to verify this doesn't cause conflicts.

## What Works Today (Single-Repo Case)

If an issue is opened in `django-polls-playwright-demo`:
- DevPod creates a workspace from `multi-repo-dev-containers` (via DEVPOD_SOURCE_REPO)
- postCreateCommand clones both repos and runs setup
- The agent sees `repo_config = {owner: "wsmoak", name: "django-polls-playwright-demo"}`
- `resolve_repo_dir` finds `/workspaces/django-polls-playwright-demo`
- `commit_and_open_pr` pushes and opens a PR in `django-polls-playwright-demo`

Similarly for `rails-otel-demo`. Each repo works independently.

## What Doesn't Work (Multi-Repo Case)

If the agent needs to change files in **both** repos and open two PRs:
- `commit_and_open_pr` only targets the webhook repo (hardcoded from configurable)
- There's no mechanism to scan for dirty repos across the workspace
- The tool would need optional `repo_owner`/`repo_name` parameters to override the default
- Or a new middleware/tool to scan `/workspaces/` for uncommitted changes and open PRs per repo

This is Phase 3 from plans/multi-repo-devcontainer-prs.md.

## What Needs to Change for Django Single-Repo Test

Likely nothing in the agent code. The flow should work because:
- The webhook fires from the django repo with the correct owner/name
- DEVPOD_SOURCE_REPO creates the multi-repo workspace
- resolve_repo_dir finds the django repo at the expected path
- commit_and_open_pr targets the django repo

The main risk is whether `_clone_or_pull_repo_in_sandbox()` handles the already-cloned repo gracefully. If it tries to clone into an existing directory, it could fail or overwrite the postCreateCommand setup.
