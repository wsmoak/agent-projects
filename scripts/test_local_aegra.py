"""Quick smoke test for local Aegra server.

Usage:
    cd /Users/wsmoak/Projects/open-swe-aws-devpod-aegra
    source .venv/bin/activate
    python /Users/wsmoak/Projects/open-swe-aws-devpod-project/scripts/test_local_aegra.py
"""

import asyncio
from langgraph_sdk import get_client


async def main():
    client = get_client(url="http://localhost:2026")

    # 1. Health check
    print("--- Health check ---")
    assistants = await client.assistants.search()
    print(f"API is reachable, assistants: {[a['graph_id'] for a in assistants]}")

    # 2. Create a thread
    print("\n--- Creating thread ---")
    thread = await client.threads.create()
    thread_id = thread["thread_id"]
    print(f"Thread ID: {thread_id}")

    # 3. Send a simple message
    print("\n--- Sending message ---")
    print("Asking the agent to list files in wsmoak/rails-otel-demo...")
    print("(Streaming response below)\n")

    last_ai_message = None
    async for event in client.runs.stream(
        thread_id=thread_id,
        assistant_id="agent",
        input={
            "messages": [
                {
                    "role": "user",
                    "content": "List the top-level files in the repo. Just ls and report back, nothing else.",
                }
            ]
        },
        config={
            "configurable": {
                "repo": {"owner": "wsmoak", "name": "rails-otel-demo"},
            }
        },
    ):
        if event.event == "values" and isinstance(event.data, dict):
            messages = event.data.get("messages", [])
            for msg in messages:
                if msg.get("type") == "ai" and msg.get("content"):
                    last_ai_message = msg["content"]
        elif event.event == "end":
            print(f"Status: {event.data.get('status', 'unknown')}")

    if last_ai_message:
        print(f"\n--- Agent response ---\n{last_ai_message}")
    else:
        print("\n--- No AI response found ---")

    print("\n--- Done ---")


if __name__ == "__main__":
    asyncio.run(main())
