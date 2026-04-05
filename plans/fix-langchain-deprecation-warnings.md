# Fix LangChain Deprecation Warnings

## Context

The OpenSWE agent logs show two warnings during issue processing. One is fixable in our code; the other is a library-internal issue.

## Warning 1: `.text()` deprecation (fix)

**File:** `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/middleware/ensure_no_empty_msg.py` line 54

**Change:** `last_msg.text()` -> `last_msg.text`

LangChain 1.2.15 deprecated calling `.text()` as a method on messages. The property `.text` returns the same value.

## Warning 2: Pydantic serialization (suppress)

The `PydanticSerializationUnexpectedValue` warning about `field_name='context'` originates from langgraph's `Runtime` dataclass (`langgraph/runtime.py` line 163) where the `context` field is typed as a generic but defaults to `None`. When the state schema is serialized, Pydantic complains about the type mismatch.

This is not our code, but we can suppress it. There is already a warnings filter section in `agent/server.py` (lines 18-23) that suppresses other library warnings.

**File:** `/Users/wsmoak/Projects/open-swe-aws-devpod-aegra/agent/server.py` line 23

**Add after line 23:**
```python
warnings.filterwarnings("ignore", message=".*PydanticSerializationUnexpectedValue.*", category=UserWarning)
```

## Steps

1. Edit `agent/middleware/ensure_no_empty_msg.py` line 54: `.text()` -> `.text`
2. Edit `agent/server.py`: add Pydantic serialization warning filter after line 23
3. Build, push, deploy, watch, test

## Verification

After deploying, create a test issue and confirm neither warning appears in the logs.
