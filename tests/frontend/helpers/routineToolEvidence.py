"""Read native checkpoint messages and expose only tool calls for one routine."""

import asyncio
import json
import os
import sys

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

URL = os.environ.get("PROGENA_DATABASE_URL", "postgresql://progena:progena@127.0.0.1:15433/progena")


async def main():
    routine_id = sys.argv[1]
    with psycopg.connect(URL) as connection:
        rows = connection.execute(
            "select thread_id, checkpoint_ns, max(checkpoint_id) as latest from checkpoints group by thread_id, checkpoint_ns order by latest desc limit 60"
        ).fetchall()
    evidence = []
    async with AsyncPostgresSaver.from_conn_string(URL) as saver:
        for thread, namespace, _ in rows:
            checkpoint = await saver.aget_tuple(
                {"configurable": {"thread_id": thread, "checkpoint_ns": namespace}}
            )
            if not checkpoint:
                continue
            messages = checkpoint.checkpoint.get("channel_values", {}).get("messages", [])
            calls = [call for message in messages for call in getattr(message, "tool_calls", [])]
            matching = [
                dict(call, order=index)
                for index, call in enumerate(calls)
                if routine_id in json.dumps(call.get("args", {}))
            ]
            if matching:
                evidence.append({"thread_id": thread, "namespace": namespace, "calls": matching})
    print(json.dumps(evidence, default=str))


asyncio.run(main())
