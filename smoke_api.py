#!/usr/bin/env python3
"""Check the Adaption API before spending real money. Costs a few cents.

    export ADAPTION_API_KEY_1=pt_live_...   # (and _2 for the second account)
    export ADAPTION_MODEL=qwen3.5-397b
    python smoke_api.py            # per account: model list, tool-call round trip, usage, reasoning, rate-limit headers
    python smoke_api.py --burst 8  # also fire 8 parallel tiny requests on account 1 to see 429s

Do NOT set ADAPTION_BASE_URL unless the provider told you to: the SDK reads it and would
send requests there instead of its default endpoint.
"""
import argparse
import json
import os
import threading

from adaption import Adaption

ECHO = {"type": "function", "name": "echo", "description": "Echo the given text back.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}

ap = argparse.ArgumentParser()
ap.add_argument("--burst", type=int, default=0)
ap.add_argument("--stream-test", action="store_true",
                help="one streamed request that generates for >60 s: checks the gateway 504 is avoided "
                     "and that response.completed carries output + usage (costs ~2 cents)")
args = ap.parse_args()

model = os.environ.get("ADAPTION_MODEL", "qwen3.5-397b")
extra = json.loads(os.environ["ADAPTION_EXTRA_BODY"]) if os.environ.get("ADAPTION_EXTRA_BODY") else None
keys = [os.environ.get(k) for k in ("ADAPTION_API_KEY_1", "ADAPTION_API_KEY_2")]
if not any(keys) and os.environ.get("ADAPTION_API_KEY"):
    keys = [os.environ["ADAPTION_API_KEY"], None]


def dump(x):
    return x.model_dump(exclude_unset=True, mode="json") if hasattr(x, "model_dump") else x


def create(client, **kw):
    if extra:
        kw["extra_body"] = extra
    return client.responses.with_raw_response.create(**kw)


for i, key in enumerate(keys, 1):
    if not key:
        print(f"account {i}: key not set, skipped")
        continue
    client = Adaption(api_key=key, timeout=300.0)
    print(f"\n=== account {i} ===")

    try:
        ids = [m.id for m in client.models.list(type="hosted").data]
        print("hosted models for this key:", ids)
        print("requested model available:", model in ids)
    except Exception as e:  # noqa: BLE001
        print("models.list failed:", repr(e))

    history = [{"role": "user", "content": "Call the echo tool with the text 'hello', then tell me what it returned."}]
    raw = create(client, model=model, input=history, tools=[ECHO], tool_choice="auto", max_output_tokens=2048)
    r = raw.parse()
    rl = {k: v for k, v in raw.headers.items() if "limit" in k.lower() or "remaining" in k.lower() or "retry" in k.lower()}
    print("rate-limit style headers:", rl or "none seen")
    print("status:", r.status, "| model reported:", r.model)
    print("output item types:", [o.type for o in r.output])
    print("usage:", dump(r.usage))
    for o in r.output:
        if o.type == "reasoning":
            d = dump(o)
            print("reasoning item keys:", sorted(d), "| summary:", str(d.get("summary"))[:200], "| content:", str(d.get("content"))[:400],
                  "| has encrypted_content:", bool(d.get("encrypted_content")))
    calls = [o for o in r.output if o.type == "function_call"]
    print("function_calls:", [(c.name, c.arguments, c.call_id) for c in calls])

    if calls:
        history += [dump(o) for o in r.output]
        for c in calls:
            history.append({"type": "function_call_output", "call_id": c.call_id, "output": "hello"})
        r2 = create(client, model=model, input=history, tools=[ECHO], max_output_tokens=2048).parse()
        print("round trip OK, final text:", (r2.output_text or "")[:200])
        print("round-trip usage:", dump(r2.usage))
    else:
        print("!! no function_call came back; tool calling needs investigation")

if args.burst and keys[0]:
    client = Adaption(api_key=keys[0], timeout=120.0, max_retries=0)
    res = []

    def hit():
        try:
            create(client, model=model, input="Say OK.", max_output_tokens=16)
            res.append("ok")
        except Exception as e:  # noqa: BLE001
            res.append(f"{type(e).__name__} {getattr(e, 'status_code', '')}")

    ts = [threading.Thread(target=hit) for _ in range(args.burst)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    print(f"\nburst of {args.burst} parallel requests:", {x: res.count(x) for x in set(res)})


if args.stream_test and keys[0]:
    import time
    client = Adaption(api_key=keys[0], timeout=600.0, max_retries=0)
    t0, types, final = time.time(), {}, None
    kw = dict(model=model, stream=True, max_output_tokens=7000,
              input="Write the integers from 1 to 4000 separated by single spaces. Output only the numbers.")
    if extra:
        kw["extra_body"] = extra
    try:
        for ev in client.responses.create(**kw):
            types[ev.type] = types.get(ev.type, 0) + 1
            if ev.type in ("response.completed", "response.incomplete"):
                final = ev.response
        dt = time.time() - t0
        print(f"\nstream finished after {dt:.0f}s; event types: {types}")
        if final is None:
            print("!! no response.completed event")
        else:
            print("final status:", final.status, "| output item types:", [o.type for o in final.output])
            print("final usage:", dump(final.usage))
            print("OK: stream survived >60 s" if dt > 62 else "note: finished under 60 s, so this did not exercise the gateway limit; raise the number range")
    except Exception as e:  # noqa: BLE001
        print(f"\nstream failed after {time.time()-t0:.0f}s:", repr(e)[:400])
