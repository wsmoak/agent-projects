#!/usr/bin/env python3
"""Smoke test for the DevPod sandbox integration.

Tests the DevPod CLI operations that DevPodBackend uses:
  - devpod up   (workspace creation)
  - devpod ssh  (command execution)
  - devpod delete (workspace cleanup)

Run with:
  python test_devpod_sandbox.py

Prerequisites:
  - DevPod CLI installed and in PATH
  - Docker provider added: devpod provider add docker
  - Docker running locally (or set DEVPOD_PROVIDER and DEVPOD_WORKSPACE_IMAGE)

Environment variables:
  DEVPOD_PROVIDER         Provider to use (default: docker)
  DEVPOD_WORKSPACE_IMAGE  Image to use (default: ubuntu:22.04)
  DEVPOD_WORKSPACE_NAME   Workspace name to use (default: openswe-test-<timestamp>)
  SKIP_CLEANUP            Set to any value to leave the workspace running after the test
"""

import os
import subprocess
import sys
import time

PROVIDER = os.getenv("DEVPOD_PROVIDER", "docker")
IMAGE = os.getenv("DEVPOD_WORKSPACE_IMAGE", "ubuntu:22.04")
WORKSPACE_NAME = os.getenv("DEVPOD_WORKSPACE_NAME", f"openswe-test-{int(time.time())}")
SKIP_CLEANUP = os.getenv("SKIP_CLEANUP")

DEVPOD_UP_TIMEOUT = 300
DEVPOD_SSH_TIMEOUT = 60


def run(args: list[str], timeout: int, input: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=(input is None),
        input=input,
        timeout=timeout,
    )


def ok(label: str) -> None:
    print(f"  [ok] {label}")


def fail(label: str, detail: str) -> None:
    print(f"  [FAIL] {label}")
    if detail:
        print(f"         {detail.strip()}")


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def check_devpod_installed() -> bool:
    result = subprocess.run(["devpod", "version"], capture_output=True, text=True)
    if result.returncode == 0:
        ok(f"devpod installed: {result.stdout.strip()}")
        return True
    fail("devpod not found in PATH", result.stderr)
    return False


def check_provider() -> bool:
    result = subprocess.run(
        ["devpod", "provider", "list", "--output", "plain"],
        capture_output=True, text=True,
    )
    if PROVIDER in result.stdout:
        ok(f"provider '{PROVIDER}' is configured")
        return True
    fail(
        f"provider '{PROVIDER}' not found",
        f"Run: devpod provider add {PROVIDER}",
    )
    return False


def create_workspace() -> bool:
    print(f"  Creating workspace '{WORKSPACE_NAME}' (provider={PROVIDER}, image={IMAGE})...")
    result = run(
        [
            "devpod", "up", WORKSPACE_NAME,
            "--provider", PROVIDER,
            "--ide", "none",
            "--source", f"image:{IMAGE}",
        ],
        timeout=DEVPOD_UP_TIMEOUT,
    )
    if result.returncode == 0:
        ok("workspace created")
        return True
    fail("workspace creation failed", result.stderr or result.stdout)
    return False


def execute_command(command: str) -> tuple[str, int]:
    # Redirect command stderr to stdout inside the shell so DevPod's own
    # status messages (written to the subprocess stderr) stay separate and
    # can be ignored.
    wrapped = f"{{ {command}; }} 2>&1"
    result = run(
        ["devpod", "ssh", WORKSPACE_NAME, "--command", wrapped],
        timeout=DEVPOD_SSH_TIMEOUT,
    )
    # result.stderr contains only DevPod's own status messages — ignore it.
    return (result.stdout or "").strip(), result.returncode


def delete_workspace() -> bool:
    print(f"  Deleting workspace '{WORKSPACE_NAME}'...")
    result = run(
        ["devpod", "delete", WORKSPACE_NAME, "--force"],
        timeout=120,
    )
    if result.returncode == 0:
        ok("workspace deleted")
        return True
    fail("workspace deletion failed", result.stderr or result.stdout)
    return False


def main() -> int:
    print("\nDevPod sandbox smoke test")
    print(f"  workspace : {WORKSPACE_NAME}")
    print(f"  provider  : {PROVIDER}")
    print(f"  image     : {IMAGE}")

    failures = 0

    # --- Pre-flight ---
    section("1. Pre-flight checks")
    if not check_devpod_installed():
        print("\nInstall DevPod: https://devpod.sh/docs/getting-started/install")
        return 1
    if not check_provider():
        return 1

    # --- Create ---
    section("2. Create workspace")
    if not create_workspace():
        return 1

    # --- Execute commands ---
    section("3. Execute commands via SSH")

    tests = [
        ("echo hello", "hello", "basic echo"),
        ("uname -s", None, "uname"),
        ("pwd", None, "pwd"),
        ("which bash", None, "bash available"),
        ("echo $((2 + 2))", "4", "arithmetic"),
        ("mkdir -p /tmp/testdir && ls /tmp/testdir && echo ok", "ok", "mkdir and ls"),
    ]

    for command, expected, label in tests:
        output, exit_code = execute_command(command)
        if exit_code != 0:
            fail(label, f"exit_code={exit_code}, output={output!r}")
            failures += 1
        elif expected is not None and expected not in output:
            fail(label, f"expected {expected!r} in output, got {output!r}")
            failures += 1
        else:
            ok(f"{label}: {output!r}" if output else label)

    # --- Write via stdin (mirrors BaseSandbox heredoc write) ---
    section("4. Write file via stdin pipe")
    content = "hello from devpod\n"
    write_cmd = f"tee /tmp/test_write.txt"
    result = subprocess.run(
        ["devpod", "ssh", WORKSPACE_NAME, "--command", write_cmd],
        input=content.encode(),
        capture_output=True,
        timeout=DEVPOD_SSH_TIMEOUT,
    )
    if result.returncode == 0:
        ok("wrote /tmp/test_write.txt via stdin")
        # Verify
        output, exit_code = execute_command("cat /tmp/test_write.txt")
        if "hello from devpod" in output:
            ok("verified file contents")
        else:
            fail("file contents wrong", output)
            failures += 1
    else:
        fail("stdin write failed", result.stderr.decode() if result.stderr else "")
        failures += 1

    # --- Cleanup ---
    section("5. Cleanup")
    if SKIP_CLEANUP:
        print(f"  SKIP_CLEANUP set — leaving workspace '{WORKSPACE_NAME}' running")
    else:
        if not delete_workspace():
            failures += 1

    # --- Summary ---
    section("Summary")
    if failures == 0:
        print("  All tests passed.")
        return 0
    else:
        print(f"  {failures} test(s) failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
