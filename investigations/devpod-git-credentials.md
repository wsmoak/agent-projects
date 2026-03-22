# Investigation: DevPod Git Credentials Architecture

## How DevPod Handles Git Credentials

DevPod uses a **secure proxy architecture** for git credentials. The container never has direct access to credentials -- instead, it proxies requests back to the host machine through a gRPC tunnel.

### The Flow

```
CONTAINER                              HOST (developer laptop)
=========                              ======================
git push
  -> credential helper
     (devpod agent git-credentials)
  -> HTTP to localhost:<port>
     (credentials server in container)
  -> gRPC tunnel to host              -> tunnelServer.GitCredentials()
                                       -> git credential fill
                                       -> macOS Keychain / Windows Credential Manager
                                       <- username + password/token
  <- credentials flow back
  <- git push authenticated
```

### Key Components

| Component | Location in DevPod source | Role |
|-----------|--------------------------|------|
| `devpod up --inject-git-credentials` | `cmd/up.go` | Flag controlling whether to set up credential proxy |
| `devpod agent git-credentials` | `cmd/agent/git_credentials.go` | Credential helper installed in container, responds to git's credential protocol |
| Credentials server | `pkg/credentials/server.go` | HTTP server in container, listens on localhost, proxies to host via tunnel |
| Tunnel server | `pkg/agent/tunnelserver/tunnelserver.go` | Host-side gRPC handler, calls `git credential fill` on host |
| Git config setup | `pkg/gitcredentials/gitcredentials.go` | Configures `git config --system credential.helper` in container |
| Container setup | `cmd/agent/container/setup.go` | Starts credentials server during container init, configures helper |

### Configuration

- **Default**: Credential injection is **enabled** (`InjectGitCredentials != "false"`)
- **Disable globally**: `devpod context set-options default -o SSH_INJECT_GIT_CREDENTIALS=false`
- **Disable per-provider**: Set `agent.injectGitCredentials: false` in provider config
- **Environment variable**: `SSH_INJECT_GIT_CREDENTIALS=false` disables injection

### Ports

- Default credentials server port: 12049
- Random range for container setup: 13000-17000
- `DEVPOD_GIT_HELPER_PORT` env var tells the helper where to reach the server

## Why This Doesn't Work for Our Deployment

Our "host" is an **ECS Fargate container**, not a developer's laptop. There is:
- No macOS Keychain or credential store on Fargate
- No interactive user session
- No tunnel back to a real machine with stored credentials

When DevPod's credential helper tries to proxy back to the host, `git credential fill` on Fargate finds nothing. The credential helper returns empty results, and git operations requiring auth (push) fail.

### Error observed in logs

```
The credential helper (devpod agent git-credentials) is returning errors...
```

## Solution

### Short-term (implemented)

1. Write a `/tmp/.git-credentials` file with the GitHub App installation token (already done during clone)
2. Set `git config --global credential.helper 'store --file=/tmp/.git-credentials'` before clone
3. Set `git config credential.helper 'store --file=/tmp/.git-credentials'` at the repo level after clone (overrides DevPod's system-level helper)
4. Don't clean up the credentials file -- let it persist for the sandbox lifetime (sandbox is ephemeral)

### Long-term (implemented)

`SSH_INJECT_GIT_CREDENTIALS` is a **DevPod context option**, not a shell environment variable. Setting it as an ECS env var has no effect — DevPod reads it from its internal config store via `devPodConfig.ContextOption()`.

To disable credential injection, run before `devpod up`:
```
devpod context set-options default -o SSH_INJECT_GIT_CREDENTIALS=false
```

**Code change** in `/Users/wsmoak/Projects/open-swe-with-devpod/agent/integrations/devpod.py`:
Added `_disable_git_credential_injection()` which runs the above command during workspace creation.

Note: The ECS env var `SSH_INJECT_GIT_CREDENTIALS=false` in ecs.tf is harmless but ineffective — can be removed later.

### Alternative: Use DevPod's credential proxy properly

To make DevPod's proxy work, we would need to:
1. Store the GitHub token in the Fargate container's git credential store before running `devpod up`
2. Or set `DEVPOD_GIT_HELPER_PORT` and run our own credentials server

This is significantly more complex than just writing a credential file directly, with no clear benefit since the sandbox is already ephemeral.
