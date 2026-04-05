# Plan: Add Test Verification Step Before PR Submission

## Context

The OpenSWE agent's system prompt tells it to "Verify -- Run linters and only tests directly related to the files you changed" (prompt.py:55), but this is advisory only. The agent can skip it, claim tests passed without running them, or run them and ignore failures. There is no enforcement.

The django-polls-playwright-demo PR #7 is an example: the agent claimed "All three tests pass locally" but we found no pytest output in the CloudWatch logs. The agent may have written correct code, but we have no proof it verified that.

## Current Architecture

1. **Agent loop**: Model calls tools in a loop (execute, read_file, write_file, etc.)
2. **commit_and_open_pr tool** (`agent/tools/commit_and_open_pr.py`): Called by the agent when it considers work done. Commits, pushes, opens PR. No verification gate.
3. **open_pr_if_needed middleware** (`agent/middleware/open_pr.py`): After-agent safety net that commits+pushes if the tool wasn't called.
4. **ensure_no_empty_msg middleware** (`agent/middleware/ensure_no_empty_msg.py`): Enforces that the agent calls commit_and_open_pr before posting completion messages.

The agent has an `execute` tool that runs shell commands in the DevPod workspace via SSH. It already uses this for linting, git operations, etc.

## Problem

"Verify" is step 3 of 5 in the prompt instructions, but it's entirely honor-system. An LLM can (and does) skip verification or hallucinate that tests passed.

## Approach: Enforce Test Execution in commit_and_open_pr

Add a mandatory verification step inside `commit_and_open_pr` that runs before committing. This is the simplest insertion point because every successful PR goes through this tool.

### What the verification step does

1. **Detect test command**: Look for signals in the repo to determine the test command:
   - `Makefile` targets (`make test`, `make check`)
   - `pytest.ini` / `pyproject.toml` `[tool.pytest]` section -> `pytest`
   - `package.json` `scripts.test` -> `yarn test` or `npm test`
   - `.devcontainer/devcontainer.json` custom properties or postCreateCommand
   - Fall back to: no tests detected, log a warning but proceed

2. **Check if test files were modified on the branch**: Use `git diff --name-only` against the base branch to get changed files. Check if any are test files using naming conventions (`test_*.py`, `*_test.go`, `*.test.ts`, `__tests__/`). This is the hard rule: **if test files were modified or added in the diff, those tests MUST pass. No PR without passing tests.**

3. **Run the modified test files**: Execute the detected test command scoped to the modified test files (e.g., `pytest tests/polls/test_ui.py --no-colors`). Use the existing `sandbox_backend.execute()` with a reasonable timeout (e.g., 600s).

4. **Gate the PR on results**:
   - Tests pass (exit code 0): Proceed with commit and PR. Include test output in the PR body.
   - Tests fail (non-zero exit): Return an error dict like `{"success": False, "error": "Tests failed:\n<output>", "pr_url": None}`. The agent receives this and can attempt to fix, then call `commit_and_open_pr` again.
   - No test files in the diff: Proceed normally (no gate).

### Files to modify

| File | Change |
|------|--------|
| `agent/tools/commit_and_open_pr.py` | Add verification logic before the commit step (between line 140 and 142). Needs access to `sandbox_backend` and `repo_dir` which are already available at that point. |
| `agent/utils/test_discovery.py` (new) | Test command detection and test file mapping logic. Keep it simple -- pattern matching, not AST analysis. |

### Implementation detail for commit_and_open_pr.py

Insert after the "no changes detected" check (line 140) and before the branch checkout (line 142):

```python
# Run verification if tests exist
from agent.utils.test_discovery import discover_and_run_tests

verification = discover_and_run_tests(sandbox_backend, repo_dir)
if verification["status"] == "failed":
    return {
        "success": False,
        "error": f"Test verification failed:\n{verification['output']}",
        "pr_url": None,
    }
# verification["status"] is "passed" or "skipped" -- proceed
```

### What test_discovery.py does

```
discover_and_run_tests(sandbox_backend, repo_dir) -> dict
```

1. Run `git diff --name-only HEAD` to get changed files
2. For each changed file, look for corresponding test files using naming conventions
3. Detect the test runner from project config files
4. Run the scoped test command via `sandbox_backend.execute()`
5. Return `{"status": "passed"|"failed"|"skipped", "output": "...", "test_files": [...]}`

### Edge cases

- **No test runner detected**: status="skipped", proceed with PR
- **No test files in the diff**: status="skipped", proceed with PR -- the gate only applies when the agent modified or added test files
- **Test command times out**: status="failed", block the PR, return error to agent
- **Agent already ran tests**: Tests run again -- this is fine, idempotent verification is the point
- **Flaky tests**: The agent gets the failure and can retry. If still failing, it reports the failure and does not open a PR.

## What this does NOT do

- Does not run the full test suite (CI handles that)
- Does not add a separate "verifier agent" as a distinct graph node -- that's a larger refactor for later
- Does not change the agent's prompt instructions -- they stay as guidance, but now there's enforcement too

## Verification

1. Deploy the change
2. Create a test issue in django-polls-playwright-demo asking to add a UI test (similar to issue #6)
3. Check CloudWatch logs for pytest output appearing before the commit
4. Verify the PR body or commit message references test results
5. Test the failure path: create an issue that would produce a failing test and confirm the agent attempts to fix it before opening a PR
