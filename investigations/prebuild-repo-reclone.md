# Investigation: Prebuild Repo Gets Removed and Re-cloned

**Date**: 2026-04-05
**Status**: Resolved

## Problem

When using devcontainer git-source mode with a prebuild, the agent removes the repo directory and clones it fresh instead of using the repo that should already be present in the prebuilt container.

## Logs

```
12:22:18 INFO  Resolved sandbox work dir to /workspaces
12:22:20 WARN  Repo directory missing or not a valid git repo at /workspaces/rails-otel-demo, removing
12:22:21 INFO  Removed invalid directory, will clone fresh repo
12:22:21 INFO  Cloning repo wsmoak/rails-otel-demo to /workspaces/rails-otel-demo
```

## Code Path

1. `create_devpod_sandbox()` in `agent/integrations/devpod.py` runs `devpod up` with `--source git:https://github.com/wsmoak/rails-otel-demo` and `--prebuild-repository <ECR repo>`.
2. DevPod starts the workspace. The prebuild image should already have the repo at `/workspaces/rails-otel-demo` with dependencies installed via `postCreateCommand`.
3. Later, `_clone_or_pull_repo_in_sandbox()` in `agent/server.py:82` is called.
4. `resolve_sandbox_work_dir()` returns `/workspaces` (via `DevPodBackend.get_work_dir()`).
5. `resolve_repo_dir()` joins to get `/workspaces/rails-otel-demo`.
6. `is_valid_git_repo()` in `agent/utils/github.py:25` runs `test -d /workspaces/rails-otel-demo/.git && echo exists`.
7. That check **fails**, triggering the remove-and-reclone path.

## Hypotheses

### H1: Volume mount overwrites prebuilt content
DevPod's git-source mode may mount a Docker volume at `/workspaces` for persistent storage. This would overwrite the prebuilt image's content. DevPod then re-clones the repo into the volume during `devpod up`, but perhaps the clone hasn't completed or the directory structure differs from what `is_valid_git_repo` expects.

### H2: `.git` is a file, not a directory
The devcontainer spec allows `.git` to be a file containing a `gitdir:` pointer (used with worktrees or submodules). The check uses `test -d` which would fail on a file.

### H3: Directory doesn't exist at all
The prebuild image may not include the repo at `/workspaces/rails-otel-demo` — it may only contain the built environment (installed dependencies, features, etc.) and DevPod handles the repo clone separately during workspace startup, which may or may not have completed by the time the agent code runs.

### H4: The repo is there but under a different path
DevPod might place the repo at a different path than `/workspaces/rails-otel-demo` (e.g., nested differently or using a workspace name instead of repo name).

## Next Step

Add diagnostic logging before the `is_valid_git_repo` check to capture:
- `ls -la /workspaces/`
- `ls -la /workspaces/rails-otel-demo/` (if it exists)
- `test -e /workspaces/rails-otel-demo/.git && file /workspaces/rails-otel-demo/.git`

This will tell us exactly what DevPod set up before the agent code tries to validate it.

## Diagnostic Results

Diagnostics confirmed **H4**: the repo was at `/workspaces/openswe-6a54794e-...` (the workspace name), not `/workspaces/rails-otel-demo`.

```
Diagnostic ls /workspaces: 
drwxr-x--- 17 vscode vscode 4096 Apr  5 12:41 openswe-6a54794e-ce9a-4950-a883-c9323aa838fd

Diagnostic ls /workspaces/rails-otel-demo: No such file or directory
```

## Fix

Changed `_generate_workspace_name()` in `agent/integrations/devpod.py` to use the repo name as the workspace name in git-source mode. This makes DevPod create the directory at `/workspaces/{repo_name}`, matching what the agent expects.

Commit: `8d276dbe` in open-swe-aws-devpod-aegra.

## Verified

Issue #115 confirmed the fix. Logs show the prebuilt repo is found and pulled instead of re-cloned:

```
Repo exists at /workspaces/rails-otel-demo, checking for uncommitted changes
Repo is clean, pulling latest changes from wsmoak/rails-otel-demo
Repo updated at /workspaces/rails-otel-demo
```
