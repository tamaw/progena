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
    since = float(sys.argv[2]) if len(sys.argv) > 2 else 0
    with psycopg.connect(URL) as connection:
        audits = connection.execute(
            "select value from store where prefix='progena.logs.tool_calls' and value::text like %s and extract(epoch from created_at) >= %s order by created_at",
            (f"%{routine_id}%", since),
        ).fetchall()
    calls = []
    for row in audits:
        payload = row[0].get("payload", {})
        calls.append(
            {
                "name": payload.get("tool_name"),
                "args": payload.get("args"),
                "success": payload.get("success"),
                "order": len(calls),
                "timestamp": payload.get("timestamp"),
            }
        )
    if calls:
        evidence.insert(0, {"source": "persisted tool-call audit", "calls": calls})
    print(json.dumps(evidence, default=str))


asyncio.run(main())
