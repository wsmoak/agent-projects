# Investigation: OpenSWE Aegra Factory Receives Empty Config

**Date:** 2026-04-04

## Summary

The OpenSWE agent deploys and receives webhooks but "takes no action." Root cause: Aegra v0.8.3 has a bug in `_load_graph_from_file` that prevents factory classification, causing every execution to use a cached minimal agent with no tools.

## Root Cause Chain

1. **Aegra v0.8.3 uses `graphs.{graph_id}` as the module name** (`spec_from_file_location(f"graphs.{graph_id}", ...)` at langgraph_service.py:427), but never creates the parent `graphs` package in `sys.modules`.

2. **Python requires parent packages to exist.** When `exec_module` runs, it throws `ModuleNotFoundError: No module named 'graphs'`.

3. **Factory classification never happens.** Because the module import fails, `classify_factory()` is never called, and `_graph_factories` stays empty for the "agent" graph.

4. **Execution falls to the static graph path.** `get_graph()` checks `if factory:` (line 370) — it's falsy, so it falls through to `_get_base_graph()` (line 316, the "Static path").

5. **`_get_base_graph` calls `get_agent` with empty config.** It eventually invokes the factory with `{"configurable": {}}` for schema extraction purposes, gets back a minimal agent, and caches it.

6. **Every execution reuses this cached empty agent.** The LLM gets called with no tools and an empty system prompt, returns a generic response, and the run completes with status=success having done nothing.

## Evidence

- CloudWatch logs show `get_agent called: thread_id=None, configurable_keys=[]` for every single invocation, including execution runs.
- The traceback in logs shows: `langgraph_service.py:316 in get_graph → # Static path — use cached base graph` and `ModuleNotFoundError: No module named 'graphs'`.
- The factory is only ever called once per run (not twice — once for schema + once for execution), confirming the factory path is never taken.

## Fix

**Aegra v0.8.6** fixes this by using `aegra_graphs.{graph_id}` namespace instead of `graphs.{graph_id}`.

- Updated `pyproject.toml` to require `aegra-cli>=0.8.6`
- Additional changes made during investigation (diagnostic logging, `is_bot_token_only_mode` fix, `thread_id` in configurable) should be kept — they fix real issues that would surface once the factory works.

## Additional Fixes Applied

1. **`is_bot_token_only_mode()` in `agent/utils/auth.py`**: Changed to return `True` when neither `X_SERVICE_AUTH_JWT_SECRET` nor `USER_ID_API_KEY_MAP` is set (regardless of `LANGSMITH_API_KEY_PROD`). Without this, the DevPod deployment falls into the LangSmith OAuth path which fails.

2. **`thread_id` in configurable dicts in `agent/webapp.py`**: Added `"thread_id": thread_id` to all four configurable dicts. While Aegra's `create_run_config` does `setdefault("thread_id", thread_id)`, this is belt-and-suspenders since the config was confirmed empty during factory invocation.

3. **Diagnostic logging in `agent/server.py`**: Added logging to `get_agent` showing `thread_id` and `configurable_keys`, and updated the no-thread_id message to say "likely schema extraction".

## Files Changed

| File | Change |
|------|--------|
| `pyproject.toml` | `aegra-cli>=0.8.0` → `aegra-cli>=0.8.6` |
| `agent/server.py` | Diagnostic logging in `get_agent` |
| `agent/utils/auth.py` | Fixed `is_bot_token_only_mode()` + diagnostic logging in `resolve_github_token` |
| `agent/webapp.py` | Added `thread_id` to all configurable dicts |

## Next Steps

1. Build and deploy with aegra-cli 0.8.6
2. Verify factory classification succeeds (look for absence of `ModuleNotFoundError`)
3. Verify `get_agent called: thread_id=<uuid>, configurable_keys=[...]` in logs
4. Verify the agent creates sandboxes and opens PRs
