"""
title: Open SWE
author: wsmoak
version: 0.9.0
description: Pipe function that connects Open WebUI to a local Aegra/OpenSWE server.
"""

import json
import os
from typing import AsyncGenerator
import httpx
from pydantic import BaseModel, Field


class Pipe:
    class Valves(BaseModel):
        AEGRA_URL: str = Field(
            default="http://host.docker.internal:2026",
            description="URL of the Aegra server",
        )
        DEFAULT_REPO_OWNER: str = Field(
            default="wsmoak",
            description="Default GitHub repo owner",
        )
        DEFAULT_REPO_NAME: str = Field(
            default="multi-repo-dev-containers",
            description="Default GitHub repo name",
        )

    def __init__(self):
        self.name = "Open SWE"
        self.valves = self.Valves(
            **{k: os.getenv(k, v.default) for k, v in self.Valves.model_fields.items()}
        )
        self._thread_map: dict[str, str] = {}

    async def on_startup(self):
        print(f"on_startup: {__name__}")

    async def on_shutdown(self):
        print(f"on_shutdown: {__name__}")

    async def _get_or_create_thread(self, chat_id: str) -> str:
        """Map an Open WebUI chat ID to an Aegra thread ID."""
        if chat_id in self._thread_map:
            return self._thread_map[chat_id]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.valves.AEGRA_URL}/threads", json={}, timeout=30
            )
            resp.raise_for_status()
            thread_id = resp.json()["thread_id"]
            self._thread_map[chat_id] = thread_id
            return thread_id

    async def pipe(self, body: dict) -> AsyncGenerator[str, None]:
        messages = body.get("messages", [])
        user_message = messages[-1]["content"] if messages else ""
        chat_id = body.get("metadata", {}).get("chat_id") or body.get("chat_id", "default")

        try:
            thread_id = await self._get_or_create_thread(chat_id)
        except Exception as e:
            yield f"Failed to create Aegra thread: {e}"
            return

        run_payload = {
            "assistant_id": "agent",
            "input": {
                "messages": [{"role": "user", "content": user_message}]
            },
            "config": {
                "configurable": {
                    "repo": {
                        "owner": self.valves.DEFAULT_REPO_OWNER,
                        "name": self.valves.DEFAULT_REPO_NAME,
                    }
                }
            },
            "stream_mode": "values",
        }

        seen_msg_ids = set()
        event_type = None

        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{self.valves.AEGRA_URL}/threads/{thread_id}/runs/stream",
                    json=run_payload,
                    timeout=300,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:") and event_type == "values":
                            try:
                                data = json.loads(line[5:].strip())
                                for msg in data.get("messages", []):
                                    msg_id = msg.get("id")
                                    if not msg_id or msg_id in seen_msg_ids:
                                        continue
                                    seen_msg_ids.add(msg_id)

                                    msg_type = msg.get("type", "")
                                    content = msg.get("content", "")

                                    if msg_type == "ai" and content:
                                        if isinstance(content, list):
                                            text_parts = [
                                                c.get("text", "")
                                                if isinstance(c, dict)
                                                else str(c)
                                                for c in content
                                            ]
                                            content = "\n".join(
                                                p for p in text_parts if p
                                            )
                                        if content:
                                            yield content + "\n\n"
                                    elif msg_type == "ai" and msg.get("tool_calls"):
                                        for tc in msg["tool_calls"]:
                                            name = tc.get("name", "unknown")
                                            args = tc.get("args", {})
                                            if isinstance(args, dict):
                                                preview = args.get(
                                                    "command",
                                                    args.get(
                                                        "query", str(args)[:100]
                                                    ),
                                                )
                                            else:
                                                preview = str(args)[:100]
                                            yield f"**Tool: {name}** `{preview}`\n\n"
                                    elif msg_type == "tool":
                                        tool_content = (
                                            content[:500] if content else ""
                                        )
                                        if tool_content:
                                            yield f"```\n{tool_content}\n```\n\n"
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            yield f"Error connecting to Aegra: {e}"
