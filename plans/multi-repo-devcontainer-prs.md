# Plan: Multi-Repo DevContainer Support with Single Agent, Multiple PRs

## Context

The OpenSWE agent currently uses DevPod with `--source image:<image>`, giving it a generic
sandbox. The agent clones the target repo at runtime and can only open a PR in that one repo.

The goal is a single agent that can:
- Work in a fully configured devcontainer environment (language runtimes, databases, services)
- Have multiple repos available in the workspace simultaneously
- Make changes across any of them and open a PR per repo as needed

This is implemented incrementally across four phases.

---

## Phase 0: Restore Working State (do first, next session)

The deployment is currently broken or needs to be re-validated after switching from the original
`open-swe` fork to our `aegra`-based fork (`open-swe-aws-devpod-aegra`).

**Steps:**
1. Verify/rebuild the Docker image from `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra`
2. Push to ECR and force a new ECS deployment
3. Verify the service is healthy: `curl https://openswe.wendysmoak.com/ok`
4. Post a test comment on https://github.com/wsmoak/rails-otel-demo/issues/85 to confirm
   the agent runs end-to-end (same test as before — ask it to add a line to README.md)

Use the `builder`, `pusher`, `deployer`, `watcher`, and `checker` subagents for this.
Do NOT run docker/aws commands directly — they produce too much output.

---

## Phase 1: Simple DevContainer (`--source git:`)

### Motivation

Enable DevPod to clone the target repo and use its `.devcontainer/devcontainer.json` so the
agent gets a project-specific environment (correct language runtime, pre-installed deps, etc.)
instead of a generic sandbox image.

### What changes in devpod.py

File: `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/integrations/devpod.py`

Add support for `DEVPOD_SOURCE_REPO` env var. In `create_devpod_sandbox`, the `devpod up`
command changes its `--source` argument:

| Mode | Env var set | `--source` arg |
|------|------------|----------------|
| Image (current) | `DEVPOD_SOURCE_IMAGE` or neither | `image:<image>` |
| Git (new) | `DEVPOD_SOURCE_REPO=https://github.com/owner/repo` | `git:<repo-url>` |

When using `--source git:`, DevPod clones the repo itself — the agent must NOT re-clone it.
The repo will be at `/workspaces/<repo-name>` inside the container.

Also: add `--devcontainer-path` passthrough for cases where the devcontainer.json is not at
the default location (`.devcontainer/devcontainer.json`). Env var: `DEVPOD_DEVCONTAINER_PATH`.

### The devcontainers repo pattern

Rather than adding `.devcontainer/` directly to application repos, use a dedicated
`wsmoak-devcontainers` repo. This repo is mostly empty except for devcontainer configurations.
DevPod is pointed at it via `DEVPOD_SOURCE_REPO=https://github.com/wsmoak/wsmoak-devcontainers`.

The devcontainer's `postCreateCommand` clones the actual application repo(s) into the workspace:

```json
{
  "name": "rails-otel-demo",
  "image": "mcr.microsoft.com/devcontainers/ruby:3.3",
  "postCreateCommand": "git clone https://github.com/wsmoak/rails-otel-demo /workspaces/rails-otel-demo && cd /workspaces/rails-otel-demo && bundle install && bin/rails db:prepare"
}
```

The workspace ends up with:
```
/workspaces/
  wsmoak-devcontainers/   ← cloned by DevPod from --source git:
  rails-otel-demo/        ← cloned by postCreateCommand
```

The webhook that triggers the agent fires on `wsmoak/rails-otel-demo` (the issue is there).
DevPod is pointed at the devcontainers repo instead. The `repo` config still identifies
`wsmoak/rails-otel-demo` as the target for PRs.

### Skip re-clone logic

The agent currently clones the target repo in its setup step. With the devcontainers pattern,
`postCreateCommand` already cloned `rails-otel-demo`. The agent must not clone it again.

Change the clone logic from "always clone" to "clone only if the directory does not already
exist." This handles both cases:
- Devcontainer postCreateCommand already cloned it → directory exists → skip
- Image-based sandbox (current behavior) → directory does not exist → clone as before

Find where the clone happens in the graph/agent setup code (explore during implementation).

### Test

Set in ECS task definition (or locally):
```
DEVPOD_SOURCE_REPO=https://github.com/wsmoak/rails-otel-demo
SANDBOX_TYPE=devpod
```

Post test comment on issue #85. Verify:
- DevPod workspace is created from the git source
- `bundle install` ran (check `bundle exec ruby --version` via agent execute)
- Agent can edit files, commit, push, open PR

---

## Phase 2: Complex DevContainer (Docker Compose, Multi-Service)

### Motivation

Some projects need external services (databases, message brokers) running alongside the app.
DevPod supports `dockerComposeFile` in devcontainer.json — this is the mechanism for that.

### Devcontainer pattern

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

DevPod supports this with both the docker provider (local) and aws provider (EC2 with Docker
installed on the instance).

### AWS provider consideration

The EC2 instance type may need to be larger for compose-based environments that run multiple
services. This is configurable via the DevPod AWS provider option `AWS_DISK_SIZE` and instance
type. Consider adding `DEVPOD_AWS_INSTANCE_TYPE` env var passthrough to `devpod.py`.

### Test

Create a compose-based devcontainer for rails-otel-demo that adds a Postgres service.
Verify the agent can run `bin/rails db:migrate` and tests against the live database.

---

## Phase 3: Multi-Repo Workspace

### Motivation

The agent needs to work across two repos simultaneously and open a PR in each. The approach:
one agent, one devcontainer, multiple repos cloned side-by-side, multiple calls to
`commit_and_open_pr` (one per repo).

### 3a. Workspace setup via devcontainer

The `wsmoak-devcontainers` repo's devcontainer.json lists all repos in `postCreateCommand`:

```json
{
  "name": "multi-repo",
  "image": "mcr.microsoft.com/devcontainers/base:ubuntu",
  "postCreateCommand": "git clone https://github.com/acme/repo-a /workspaces/repo-a && git clone https://github.com/acme/repo-b /workspaces/repo-b"
}
```

The workspace ends up with:
```
/workspaces/
  wsmoak-devcontainers/   ← cloned by DevPod from --source git:
  repo-a/                 ← cloned by postCreateCommand
  repo-b/                 ← cloned by postCreateCommand
```

Git credentials are already configured globally for github.com, so all repos can push.
The agent is pointed at the devcontainers repo regardless of which app repo triggered the task.

### 3b. `commit_and_open_pr` tool changes

File: `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/tools/commit_and_open_pr.py`

Add optional `repo_owner: str | None = None` and `repo_name: str | None = None` params.
Fall back to `config.configurable.repo` if not provided (preserves existing behavior).

The branch name `open-swe/{thread_id}` is the same in both repos — that is fine since they
are different repos. No collision.

The tool can be called multiple times in one agent run:
```
commit_and_open_pr(title="fix: ...", body="...", repo_name="repo-a")
commit_and_open_pr(title="fix: ...", body="...", repo_name="repo-b", repo_owner="acme")
```

### 3c. `open_pr_if_needed` middleware changes

File: `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/middleware/open_pr.py`

The fallback middleware currently handles exactly one repo from config. For multi-repo it needs
to scan the workspace for git repos that have uncommitted changes and open a PR for each.

Strategy:
1. Run `find /workspaces -maxdepth 2 -name ".git" -type d` in the sandbox
2. For each git repo found, run `git remote get-url origin` to extract `owner/repo`
3. Check for uncommitted changes; if present, commit, push, and open a PR

This approach requires no config — it derives owner/name from the git remote URL.

### 3d. System prompt changes

File: `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/prompt.py`

- `WORKING_ENV_SECTION`: change "working directory: `{working_dir}`" to describe all repos
  in the workspace when multiple are present. Pass `additional_repos` list to
  `construct_system_prompt`; inject a "Repos available" section if the list is non-empty.

- `COMMIT_PR_SECTION`: add a note explaining that when working across multiple repos, call
  `commit_and_open_pr` once per repo, passing `repo_owner` and `repo_name` explicitly.

- `TOOL_USAGE_SECTION`: update the `commit_and_open_pr` entry to document the new optional
  params.

### 3e. Passing additional repo info to the agent

In the webhook handler (`webapp.py`), read an env var `DEVPOD_EXTRA_REPOS` (comma-separated
`owner/repo` pairs) and pass it through to `construct_system_prompt`. This tells the agent
at startup which repos are in the workspace without requiring it to discover them.

Example:
```
DEVPOD_EXTRA_REPOS=acme/repo-b,acme/repo-c
```

---

## Implementation Order

```
[ ] Phase 0: Restore working state (infra up, aegra fork deployed, end-to-end test passing)
[ ] Phase 1: Simple devcontainer
    [ ] Create wsmoak-devcontainers repo on GitHub
    [ ] Add .devcontainer/devcontainer.json that clones rails-otel-demo and runs bundle install
    [ ] Add DEVPOD_SOURCE_REPO support to devpod.py
    [ ] Change clone logic to skip if directory already exists (not gated on env var)
    [ ] Test: agent runs in devcontainer, bundle install happened, PR opened in rails-otel-demo
[ ] Phase 2: Complex devcontainer (Docker Compose + Postgres)
    [ ] Add DEVPOD_AWS_INSTANCE_TYPE passthrough to devpod.py
    [ ] Create compose-based devcontainer for rails-otel-demo
    [ ] Test: agent can run db:migrate, tests pass against live DB
[ ] Phase 3: Multi-repo
    [ ] commit_and_open_pr: add optional repo_owner/repo_name params
    [ ] open_pr_if_needed: scan workspace, open PR per dirty repo
    [ ] prompt.py: update WORKING_ENV, COMMIT_PR, TOOL_USAGE sections
    [ ] webapp.py: read DEVPOD_EXTRA_REPOS, pass to construct_system_prompt
    [ ] devcontainer.json: postCreateCommand clones repo-b
    [ ] Test: agent makes changes in both repos, opens 2 PRs
```

---

## Critical Files

| File | Change |
|------|--------|
| `open-swe-aws-devpod-aegra/agent/integrations/devpod.py` | DEVPOD_SOURCE_REPO, DEVPOD_DEVCONTAINER_PATH, DEVPOD_AWS_INSTANCE_TYPE support |
| `open-swe-aws-devpod-aegra/agent/tools/commit_and_open_pr.py` | Optional repo_owner/repo_name params |
| `open-swe-aws-devpod-aegra/agent/middleware/open_pr.py` | Scan workspace for all dirty git repos |
| `open-swe-aws-devpod-aegra/agent/prompt.py` | Multi-repo workspace section, commit_and_open_pr docs |
| `open-swe-aws-devpod-aegra/agent/webapp.py` | Read DEVPOD_EXTRA_REPOS env var |
| `wsmoak-devcontainers/.devcontainer/devcontainer.json` | New file — simple devcontainer first, then compose-based, then multi-repo |

## Key Existing Functions to Reuse

- `resolve_repo_dir(sandbox_backend, repo_name)` — `sandbox_paths.py:20` — already generic
- `git_remote get-url origin` pattern — not yet in github.py, add a small helper
- `aresolve_repo_dir` — `sandbox_paths.py:29` — use in updated middleware

---

## Verification (end-to-end for Phase 3)

1. Set env vars:
   ```
   DEVPOD_SOURCE_REPO=https://github.com/wsmoak/wsmoak-devcontainers
   DEVPOD_EXTRA_REPOS=wsmoak/repo-a,wsmoak/repo-b
   SANDBOX_TYPE=devpod
   ```
   (The devcontainer's postCreateCommand clones repo-a and repo-b)

2. Post comment on https://github.com/wsmoak/rails-otel-demo/issues/85 instructing the agent
   to make a change in repo-a AND a change in repo-b.

3. Verify:
   - DevPod workspace has repos at `/workspaces/repo-a` and `/workspaces/repo-b`
   - Agent opens a PR in `wsmoak/repo-a`
   - Agent opens a PR in `wsmoak/repo-b`
   - Agent comments back on issue #85 with both PR links
