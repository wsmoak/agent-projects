# Investigation: Agent Can't Push (Git Credential Issue)

## Symptom

During the first end-to-end run, the agent reported it couldn't push. The `commit_and_open_pr` tool has credential injection via `git_push()`, but the agent may have attempted a raw `git push` via the sandbox `execute()` tool instead.

## How Git Credentials Work Today

### Clone/Pull (server.py, `_clone_or_pull_repo_in_sandbox`)

1. `setup_git_credentials()` writes the GitHub token to `/tmp/.git-credentials`
2. Clone/pull runs with `-c credential.helper='store --file=/tmp/.git-credentials'`
3. `cleanup_git_credentials()` deletes `/tmp/.git-credentials` in a `finally` block

The credentials are **ephemeral** -- created just before clone, destroyed immediately after. This means any subsequent `git push` without re-creating credentials will fail with an auth error.

### Push via `commit_and_open_pr` tool (commit_and_open_pr.py, line 179)

This tool calls `git_push(sandbox_backend, repo_dir, target_branch, github_token)`, which:
1. Calls `setup_git_credentials()` to re-create `/tmp/.git-credentials`
2. Runs `git -c credential.helper='store --file=...' push origin <branch>`
3. Calls `cleanup_git_credentials()` in a `finally` block

This path works correctly -- credentials are re-created before push.

### Push via sandbox `execute()` (what the agent likely did)

The agent has access to the sandbox backend's `execute()` method as a tool. If it runs `git push` directly as a shell command, there are no credentials configured -- `/tmp/.git-credentials` was deleted after clone, and no credential helper is set globally.

## Relevant Files

| File | Role |
|------|------|
| `agent/utils/github.py:116-158` | `_CRED_FILE_PATH`, `setup_git_credentials`, `cleanup_git_credentials`, `_git_with_credentials`, `git_push` |
| `agent/server.py:67-172` | `_clone_or_pull_repo_in_sandbox` -- sets up and tears down credentials around clone/pull |
| `agent/tools/commit_and_open_pr.py:170-179` | Calls `git_push` with token -- works correctly |

## Upstream Direction: Sandbox Proxy Auth

There is an unmerged upstream branch `upstream/yogesh/github-auth-proxy` (commits `911e99ba`, `c993f281`, `c608f763`, `c993f281`) that removes all credential file handling entirely:

- Deletes `setup_git_credentials`, `cleanup_git_credentials`, `_git_with_credentials`, `_CRED_FILE_PATH`
- `git_push()` becomes a plain `git push origin <branch>` with no token parameter
- `_clone_or_pull_repo_in_sandbox` drops the `github_token` parameter and runs plain `git clone`/`git pull`
- Authentication is handled by the **LangSmith sandbox proxy** -- a built-in feature where the sandbox's HTTP traffic is transparently authenticated at sandbox creation time via a proxy-config API

The key commit message: "feat: authenticate git operations via sandbox proxy instead of credential files"

In the proxy model, `create_langsmith_sandbox(None, github_token)` passes the token at sandbox creation, and the LangSmith proxy intercepts all HTTPS requests to github.com and injects auth headers. Plain `git push` just works.

## Why This Doesn't Work for DevPod

DevPod sandboxes are plain EC2 instances (or Docker containers). There is no proxy layer. Git operations need explicit credential configuration.

Our fork's `_clone_or_pull_repo_in_sandbox` still has the credential file approach from before the proxy branch, but the cleanup-after-clone pattern means credentials don't survive to push time.

## Proposed Fix

Keep credentials alive for the sandbox session instead of cleaning them up after clone. The sandbox is ephemeral (destroyed at run end), so there's no security concern.

Two changes needed:

1. **`agent/server.py`**: In `_clone_or_pull_repo_in_sandbox`, remove the `cleanup_git_credentials` calls from the `finally` blocks. Add a `git config --global credential.helper 'store --file=/tmp/.git-credentials'` after writing credentials so any git command picks them up automatically.

2. **`agent/utils/github.py`**: `git_push()` can drop the credential setup/teardown since credentials persist from clone time. (Or keep it as a defensive measure -- it's harmless to re-write the same file.)

This way, whether the agent pushes via `commit_and_open_pr` or via a raw `git push` in the sandbox, credentials are available.
