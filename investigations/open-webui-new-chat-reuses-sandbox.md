# Investigation: New Open WebUI chat reuses sandbox and PR

**Date:** 2026-04-06
**Status:** In progress

## Problem

Starting a new chat in Open WebUI with the local Open SWE agent does not create a fresh sandbox. Instead, the new chat picks up the existing DevPod workspace with leftover state (branches, commits, credential files) from a previous chat. This caused a new chat's commits to be appended to an existing PR (wsmoak/rails-otel-demo#134) instead of creating a separate PR.

The user also observed old context (from the first chat's file listing) appearing in the new chat's responses.

## Root cause hypothesis

### Sandbox reuse via deterministic workspace names

In `agent/integrations/devpod.py:392-415`, `_generate_workspace_name` returns `repo_name.lower()` when in git-source mode. For the `rails-otel-demo` repo, every thread gets workspace name `rails-otel-demo`.

When `devpod up` is called with a workspace name that already exists, DevPod reconnects to the existing container rather than creating a fresh one. So the second thread inherits the first thread's workspace with its branches, commits, and git credential file.

The thread-id-based naming (`openswe-{thread_id}`) only applies in image mode (no repo_name), not git-source mode.

### Old context appearing in new chat

The pipe function (`scripts/openwebui_aegra_pipe.py:71`) only sends `user_message`, not the full message history. A new chat should get a new `chat_id`, which maps to a new Aegra thread via `_get_or_create_thread`. However:

1. If the pipelines container was restarted, `_thread_map` (in-memory dict, line 35) is cleared, but the Aegra thread may persist server-side.
2. Even with a new Aegra thread, if the sandbox is reused (see above), the agent sees the same workspace state and may reference prior work found in the sandbox.

Need to verify: add debug logging to the pipe function to confirm new threads are actually being created, and check whether Aegra thread state leaks across chats.

## Files involved

- `agent/integrations/devpod.py` -- `_generate_workspace_name` (line 392) and `create_devpod_sandbox` (line 264)
- `agent/utils/sandbox_state.py` -- `SANDBOX_BACKENDS` cache keyed by thread_id
- `agent/server.py` -- sandbox creation/reconnection logic (lines 360-453)
- `scripts/openwebui_aegra_pipe.py` -- thread mapping and message passing

## Possible fixes to evaluate

1. **Make workspace names unique per thread in git-source mode.** Change `_generate_workspace_name` to always include the thread ID, e.g. `openswe-{thread_id}` even when repo_name is provided. This would mean each chat gets its own container. Downside: more containers, slower startup (no reuse of cloned repos).

2. **Reset workspace state on new thread.** When a new thread creates a sandbox and gets an existing workspace, do a `git checkout main && git reset --hard origin/main && git clean -fd` to clear prior state. Downside: fragile, may miss state outside the repo dir.

3. **Delete and recreate workspace for new threads.** If `sandbox_id` is None (new thread) but the workspace name already exists, run `devpod delete` first. Downside: slower, loses cached dependencies.

4. **Add a valve to the pipe function** to let the user force a new sandbox (e.g. a "Fresh sandbox" toggle).

## Next steps

- Add debug logging to the pipe function to verify new thread creation
- Test whether the old context issue is from sandbox reuse or thread state leakage
- Decide on a fix approach and implement it
