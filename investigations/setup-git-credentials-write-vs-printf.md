# Investigation: setup_git_credentials -- write() vs printf via execute()

**Date:** 2026-04-06
**Context:** Merging upstream main into `open-swe-aws-devpod-aegra` branch

## Status

Asked in https://forum.langchain.com/t/basesandbox-write-fails-if-file-already-exists-any-way-to-overwrite/3335

## Background

During the merge of upstream `open-swe` main into our DevPod/Aegra branch, a conflict arose in `agent/utils/github.py` around the `setup_git_credentials` function. Upstream main had changed the implementation to use `sandbox_backend.write()`, while our branch uses `sandbox_backend.execute()` with `printf`. The upstream test in `tests/test_github_security.py` was written against the `write()` API and failed against our code.

## The two implementations

### Upstream main (write API)

```python
def setup_git_credentials(sandbox_backend, github_token):
    sandbox_backend.write(_CRED_FILE_PATH, f"https://git:{github_token}@github.com\n")
    sandbox_backend.execute(f"chmod 600 {_CRED_FILE_PATH}")
```

Upstream's rationale: the `write()` API sends content in the HTTP body, so the token never appears in shell history or process listings.

### Our branch (printf via execute)

```python
def setup_git_credentials(sandbox_backend, github_token):
    cred_line = f"https://git:{github_token}@github.com"
    sandbox_backend.execute(
        f"printf '%s\\n' {shlex.quote(cred_line)} > {_CRED_FILE_PATH} && chmod 600 {_CRED_FILE_PATH}"
    )
```

## Why we kept printf

The `write()` method in `BaseSandbox` (from deepagents) is documented as "Create a new file, **failing if it already exists**." The implementation (`deepagents.backends.sandbox.BaseSandbox.write`) runs a preflight existence check and returns an error if the file is already present.

This is a problem for `setup_git_credentials` because:

1. **Multiple repos in one sandbox.** When working with multi-repo DevPod workspaces, `_clone_or_pull_repo_in_sandbox` is called for each repo. Each call invokes `setup_git_credentials`. The first call creates the credentials file; subsequent calls would fail with `write()` because the file already exists.

2. **Reconnect flows.** When a sandbox is reused across thread invocations (cached in `SANDBOX_BACKENDS`), the credentials file may persist from a prior run. `write()` would fail on reconnection.

3. **The DevPod SSH path.** `DevPodBackend` inherits `write()` from `BaseSandbox`, which delegates to `execute()` internally anyway. The `write()` call runs a shell command to check existence, then calls `upload_files()` which itself runs a shell command. The token still transits through shell execution -- just with extra steps and a failure mode we don't want.

## Security considerations

Upstream's concern is valid in general: passing secrets through shell commands can expose them in `/proc/<pid>/cmdline` and shell history. However, in our case:

- **No shell history.** Commands run via `DevPodBackend.execute()` go through `subprocess.run()` with `devpod ssh --command`, not an interactive shell. There is no `.bash_history` being written.
- **The token is shlex.quoted.** The `shlex.quote()` call prevents shell injection even with adversarial token values.
- **Short-lived process.** The `printf` command executes and exits immediately. The `/proc` exposure window is minimal.
- **Same effective path.** `BaseSandbox.write()` delegates to `execute()` under the hood, so the token ends up in a subprocess either way. The only difference is that `write()` base64-encodes the path for the existence check and uses `upload_files()` which calls `execute()` with a `cat` command.

## What we changed in the test

The upstream test `test_git_pull_branch_quotes_repo_dir_and_branch_when_using_credentials` asserted that `sandbox.writes` contained the credential write. We updated it to instead verify that `sandbox.commands` contains the expected `printf` command with proper quoting. The security properties being tested (shell injection prevention via `shlex.quote`) are preserved.

## Future consideration

If `deepagents` adds an `overwrite()` or `write(overwrite=True)` method to `BaseSandbox`, we should switch to it. That would give us the cleaner API without the existence-check failure mode. Until then, `printf` via `execute()` is the correct approach for our use case.
