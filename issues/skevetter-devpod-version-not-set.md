# skevetter/devpod release binaries report v0.0.0

## Repository

skevetter/devpod

## Problem

The release binaries (e.g. v0.18.2) report `v0.0.0` because the version is not injected via ldflags at build time. The version variable lives in `pkg/version/version.go`:

```go
var version = "v0.0.0"
```

This causes a warning every time `devpod up` injects the agent into a workspace:

```
17:25:30 debug execute command locally inject.go:211
17:25:38 warn the remote agent version does not match the expected version. If your workspace fails to deploy, you may need to manually remove the existing agent and redeploy.
 expectedVersion=v0.0.0 actualVersion=v0.18.4 agentPath=/usr/local/bin/devpod tunnelserver.go:424
```

## Fix

The Go build should include `-ldflags "-X github.com/loft-sh/devpod/pkg/version.version=v0.18.2"` (or whatever the release tag is). The upstream loft-sh/devpod did this via `Taskfile.yml` — see the `DEVPOD_CLI_VERSION` variable and how it's passed to `go build`.
