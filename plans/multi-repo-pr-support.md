# Plan: Multi-Repo PR Support

## Context

OpenSWE currently targets one repo per agent run. The repo is set in the LangGraph thread configurable at webhook time and hardcoded through to `commit_and_open_pr`. When using a multi-repo devcontainer workspace (via `multi-repo-dev-containers`), the agent can see all repos but can only open PRs against the trigger repo. We need the agent to work across repos and open separate PRs in each.

Confirmed by testing on 2026-04-05: the agent said "I'm currently set up to open PRs only against wsmoak/multi-repo-dev-containers" and offered to handle the other repos as separate tasks.

## Approach

Two changes, both backward-compatible:

### 1. Add optional repo override to `commit_and_open_pr`

**File:** `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/tools/commit_and_open_pr.py`

Add optional `repo_owner` and `repo_name` parameters:

```python
def commit_and_open_pr(
    title: str,
    body: str,
    commit_message: str | None = None,
    repo_owner: str | None = None,   # NEW
    repo_name: str | None = None,    # NEW
) -> dict[str, Any]:
```

Logic at lines 119-127 changes to:

```python
repo_config = configurable.get("repo", {})
repo_owner = repo_owner or repo_config.get("owner")
repo_name = repo_name or repo_config.get("name")
```

Everything downstream (`resolve_repo_dir`, `git_push`, `create_github_pr`) already accepts arbitrary repo_owner/repo_name/repo_dir — no changes needed in those functions.

**Credentials:** `git_push` already calls `setup_git_credentials` and passes `-c credential.helper=...` as a command-line override, so it works for any repo directory regardless of repo-level git config.

Update the docstring to document the new params and explain multi-repo usage.

### 2. Create AGENTS.md in multi-repo-dev-containers

**File:** `/Users/wsmoak/Projects/multi-repo-dev-containers/AGENTS.md` (new)

This file is automatically loaded into the agent's system prompt (via `read_agents_md_in_sandbox` in `server.py:405`). It tells the agent:

- The workspace layout: which repos exist, their paths, and their GitHub owner/name
- That it should work in the sibling directories (`/workspaces/rails-otel-demo`, `/workspaces/django-polls-playwright-demo`), not in `multi-repo-dev-containers` itself
- That it must call `commit_and_open_pr` separately for each repo that has changes, passing explicit `repo_owner` and `repo_name`

Content:

```markdown
# Multi-Repo Workspace

This workspace contains multiple repositories. Your working directory is
`/workspaces/multi-repo-dev-containers` but the application code is in
sibling directories.

## Available Repositories

| Repo | Path | GitHub |
|------|------|--------|
| rails-otel-demo | /workspaces/rails-otel-demo | wsmoak/rails-otel-demo |
| django-polls-playwright-demo | /workspaces/django-polls-playwright-demo | wsmoak/django-polls-playwright-demo |

## Multi-Repo Workflow

When your task involves multiple repos:

1. Navigate to the appropriate repo directory before reading/editing files
2. When done, call `commit_and_open_pr` **separately for each repo** that has changes
3. You MUST pass `repo_owner` and `repo_name` explicitly for each call

Example:
- `commit_and_open_pr(title="feat: add API endpoint", body="...", repo_owner="wsmoak", repo_name="rails-otel-demo")`
- `commit_and_open_pr(title="feat: call new API", body="...", repo_owner="wsmoak", repo_name="django-polls-playwright-demo")`

Do NOT attempt to open a single PR in multi-repo-dev-containers for changes
that belong in the sub-repos.
```

## What Does NOT Need to Change

- **Git utilities** (`agent/utils/github.py`) — already parameterized with `repo_dir`
- **`resolve_repo_dir`** (`agent/utils/sandbox_paths.py`) — already works with any repo name
- **Credentials** — `git_push` handles credentials via command-line override
- **Slack handler** — `SLACK_REPO_OWNER=wsmoak`, `SLACK_REPO_NAME=multi-repo-dev-containers` is already set
- **`_clone_or_pull_repo_in_sandbox`** — handles already-cloned repos (pulls instead of re-cloning)
- **server.py / prompt.py** — AGENTS.md injection already works

## Prerequisites

- GitHub App (or token) must have write access to all three repos: `multi-repo-dev-containers`, `rails-otel-demo`, `django-polls-playwright-demo`
- Verify with: check the GitHub App installation settings

## Deferred: Middleware safety net

The `open_pr_if_needed` middleware (`agent/middleware/open_pr.py`) only checks the trigger repo. A future enhancement could scan all workspace repos for uncommitted changes and auto-PR them. Not needed for v1 — the AGENTS.md instructions should be sufficient.

## Verification

1. Build and deploy the updated agent image
2. In Slack, send: `@Open SWE add the current timestamp to the README of each project, both rails-otel-demo and django-polls-playwright-demo`
3. Expected: agent opens two PRs, one in each repo
4. Fallback test: send with `repo:wsmoak/rails-otel-demo` override to confirm single-repo still works

## Files to Modify

1. `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/tools/commit_and_open_pr.py` — add optional params
2. `/Users/wsmoak/Projects/multi-repo-dev-containers/AGENTS.md` — new file, workspace instructions
