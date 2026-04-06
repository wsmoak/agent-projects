# get_config() called outside of runnable context

**Date:** 2026-04-06

## Symptom

Follow-up comments on GitHub issues (second message in a thread) fail with:

```
RuntimeError: Called get_config outside of a runnable context
```

at `agent/server.py` line 308 (and line 401).

## Root cause

`get_agent()` receives a `config: RunnableConfig` parameter, but two code paths called `get_config()` instead (which reads from LangGraph thread-local storage). On first invocation, the graph is running inside LangGraph's runnable context so `get_config()` works. On follow-up messages, Aegra's `run_executor.py` calls `generate_graph` -> `get_agent()` outside the runnable context, so `get_config()` throws.

## Origin

- One `get_config()` call was in the **upstream OpenSWE** code from commit `bd52e5e0` (Drop monorepo #1029)
- The second was introduced in our commit `b930cf2b` (multi-repo PR support), which refactored from `client.threads.get()` to `get_config()`

## Fix

Replaced both `get_config().get("metadata", {})` with `config.get("metadata", {})`, using the `config` parameter already passed into `get_agent()`. Removed the unused `from langgraph.config import get_config` import.

## Notes

- The traceback was visible because Aegra 0.9.2 fixed the structlog missing tracebacks issue (aegra#295)
- This only manifests on follow-up messages to existing threads (cached sandbox path)
