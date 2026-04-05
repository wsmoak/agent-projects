# structlog JSONRenderer drops exception tracebacks

## Status

Open Issue: https://github.com/ibbybuilds/aegra/issues/295

## Description

When running in production mode (`ENV_MODE` not `LOCAL` or `DEVELOPMENT`), `logger.exception()` calls produce JSON log entries with `"exc_info": true` but **no actual traceback text**. This makes it impossible to diagnose errors from production logs.

## Root Cause

In `aegra_api/utils/setup_logging.py`, the `shared_processors` list is missing `structlog.processors.format_exc_info`. This processor is responsible for converting the `exc_info` flag into a formatted traceback string before the `JSONRenderer` serializes the log entry.

The `ConsoleRenderer` (used in LOCAL/DEVELOPMENT mode) handles `exc_info` internally, so tracebacks appear in dev but not in production JSON logs.

## Reproduction

Any `logger.exception("message")` call in production mode. For example, `run_executor.py:84`:

```python
logger.exception("Run failed", run_id=run_id)
```

Produces:

```json
{
    "event": "Run failed",
    "exc_info": true,
    "level": "error",
    ...
}
```

Expected: a `"traceback"` or `"exception"` field containing the formatted traceback.

## Fix

Add `structlog.processors.format_exc_info` to the `shared_processors` list in `setup_logging.py`, before the positional arguments formatter:

```python
shared_processors: list[Any] = [
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.CallsiteParameterAdder({...}),
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.format_exc_info,          # <-- add this
    structlog.stdlib.PositionalArgumentsFormatter(),
]
```

## Impact

All `logger.exception()` and `logger.error(..., exc_info=True)` calls across the entire application are affected. No tracebacks are visible in production JSON logs.
