# Plan: Fix OpenSWE Agent Factory for Aegra

## Context

OpenSWE was migrated from LangGraph Platform to Aegra. The agent deploys and receives webhooks, but "takes no action." The handoff document (`investigations/aegra-factory-architecture-handoff.md`) attributes this to `thread_id` being missing from the config when `get_agent()` is called.

**Key finding from code analysis: the handoff document's premise is likely wrong.** Tracing Aegra's execution path shows:

1. `execute_run_async()` calls `create_run_config(run_id, thread_id, ...)` which adds `thread_id` to `config["configurable"]` via `setdefault` (langgraph_service.py:729)
2. `get_graph()` then calls `invoke_factory(factory, graph_id, run_config, ...)` with that config (langgraph_service.py:381)
3. Aegra's integration tests verify: `assert received_config.get("configurable", {}).get("thread_id") == "t1"`

The "No thread_id" log messages are almost certainly from **schema extraction/introspection** calls (`_call_factory_with_defaults` at langgraph_service.py:581), which intentionally pass `{"configurable": {}}`. These are NOT execution calls.

The real reason the agent "takes no action" needs diagnosis.

## Plan

### Step 1: Add diagnostic logging to get_agent factory

**File:** `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/server.py`

At the top of `get_agent()` (line 258), add logging that distinguishes introspection from execution:

```python
async def get_agent(config: RunnableConfig) -> Pregel:
    thread_id = config["configurable"].get("thread_id", None)
    configurable_keys = list(config.get("configurable", {}).keys())
    logger.info(
        "get_agent called: thread_id=%s, configurable_keys=%s",
        thread_id, configurable_keys
    )
    ...
```

Change the no-thread_id branch to clearly label it as introspection:

```python
if thread_id is None:
    logger.info("No thread_id (likely schema extraction), returning minimal agent")
    return create_deep_agent(system_prompt="", tools=[]).with_config(config)
```

### Step 2: Deploy and test

Use the subagent pipeline: `builder` -> `pusher` -> `deployer` -> `watcher`

Then trigger a test run:
```
gh issue comment 94 --repo wsmoak/rails-otel-demo --body "@openswe please add a line..."
```

Watch CloudWatch logs for the new diagnostic output. This will tell us:
- Whether `thread_id` IS in the config during execution (confirming the code analysis)
- If it is, what happens after sandbox setup (where the real failure is)
- If it isn't, that there's a runtime discrepancy vs the code we're reading

### Step 3: Fix based on findings

**If thread_id IS present (most likely):** The factory works correctly. The issue is downstream — sandbox creation, tool execution, or graph behavior. The diagnostic logs from Step 1 will point to where it fails.

**If thread_id is NOT present:** There's a runtime mismatch. Most likely fix is Option 3 from the handoff — explicitly pass `thread_id` in webapp.py's configurable dict:

```python
# In webapp.py, wherever configurable is built for runs.create():
configurable = {
    "thread_id": thread_id,  # ensure it's in configurable
    # ... existing keys
}
```

This is a no-risk addition since `create_run_config` uses `setdefault` (won't overwrite).

### Step 4: Update handoff document

Update `investigations/aegra-factory-architecture-handoff.md` with findings from the diagnostic run.

## Critical Files

| File | What to change |
|------|----------------|
| `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/server.py` | Add diagnostic logging to `get_agent` (line 258) |
| `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/webapp.py` | Possibly add `thread_id` to configurable (only if Step 2 shows it's missing) |
| `/Users/wsmoak/Projects/open-swe-aws-devpod-project/investigations/aegra-factory-architecture-handoff.md` | Update with findings |

## Verification

1. Deploy with diagnostic logging
2. Trigger test run via GitHub issue comment on `wsmoak/rails-otel-demo#94`
3. Check CloudWatch logs (`/ecs/open-swe`) for:
   - `get_agent called: thread_id=<some-uuid>, configurable_keys=[...]` — confirms thread_id present
   - OR `get_agent called: thread_id=None, configurable_keys=[...]` — confirms it's missing
4. Based on which case, apply the appropriate fix and re-deploy
