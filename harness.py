#!/usr/bin/env python3
"""Single-agent harness for the Adaption Responses API.

The model loop runs on the host. Tool calls run in a podman container with no
network (or, for dry runs only, in a plain local subprocess). The answer is
graded on the host; the container never sees the solution.

API shape (https://docs.adaptionlabs.ai/inference-stacks/generate-a-model-response/):
  client.responses.create(model, instructions, input=[items], tools=[flat function tools], ...)
  response.output items: reasoning | message | function_call(call_id, name, arguments)
  we answer each function_call with {"type": "function_call_output", "call_id", "output"}.
"""
import json
import subprocess
import threading
import time
import uuid
from pathlib import Path

SYSTEM_PROMPT = """You are an autonomous agent solving a logic puzzle in a Linux sandbox.
Tools: bash (run a shell command), read_file, write_file, submit_answer.
Your working directory is /workspace; the puzzle text is also saved there as puzzle.txt.
Python 3 is installed. There is no internet access.
Work until you have an answer, then call submit_answer. You may submit up to {max_submissions} times; after each submission you are told only whether it was correct.
Do not stop without submitting an answer."""

# Responses API function tools are FLAT (no {"function": {...}} wrapper as in chat completions).
TOOLS = [
    {"type": "function", "name": "bash",
     "description": "Run a shell command in /workspace and return its output.",
     "parameters": {"type": "object", "properties": {
         "command": {"type": "string", "description": "The command to run."}},
         "required": ["command"]}},
    {"type": "function", "name": "read_file",
     "description": "Read a text file inside /workspace.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}}, "required": ["path"]}},
    {"type": "function", "name": "write_file",
     "description": "Write a text file inside /workspace (overwrites).",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]}},
    {"type": "function", "name": "submit_answer",
     "description": "Submit the final answer as a JSON string in the format the puzzle asks for.",
     "parameters": {"type": "object", "properties": {
         "answer": {"type": "string"}}, "required": ["answer"]}},
]


def plain(x):
    """SDK objects -> plain JSON-able dicts. exclude_unset mirrors what the SDK itself sends back."""
    if hasattr(x, "model_dump"):
        try:
            return x.model_dump(exclude_unset=True, mode="json")
        except TypeError:
            return x.model_dump()
    if isinstance(x, list):
        return [plain(i) for i in x]
    if isinstance(x, dict):
        return {k: plain(v) for k, v in x.items()}
    return x


def as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ---------- budget ----------

class Budget:
    """Estimated spend per account, persisted so a restart keeps counting.

    Adaption lists prices in CREDITS per 1M tokens (not stated to be dollars).
    Cached input tokens are billed at a lower rate. Cross-check with the dashboard.
    """

    def __init__(self, path, n_accounts, cap_per_account, price_in, price_out, price_cached=None):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.cap = cap_per_account
        self.price_in, self.price_out = price_in, price_out
        self.price_cached = price_in if price_cached is None else price_cached
        self.spend = [0.0] * n_accounts
        if self.path.exists():
            saved = json.loads(self.path.read_text()).get("spend", [])
            for i, v in enumerate(saved[:n_accounts]):
                self.spend[i] = v

    def cost(self, ptok, ctok, cached=0):
        cached = min(cached, ptok)
        return ((ptok - cached) * self.price_in + cached * self.price_cached + ctok * self.price_out) / 1e6

    def can_spend(self, acct):
        with self.lock:
            return self.spend[acct - 1] < self.cap

    def add(self, acct, ptok, ctok, cached=0, reported=None):
        """`reported` is usage.cost from the API (matches the price list); preferred over the estimate."""
        c = float(reported) if isinstance(reported, (int, float)) else self.cost(ptok, ctok, cached)
        with self.lock:
            self.spend[acct - 1] += c
            self.path.write_text(json.dumps({"spend": self.spend, "cap": self.cap}))
        return c

    def snapshot(self):
        with self.lock:
            return list(self.spend)


# ---------- executors ----------

def trunc(s, head=3000, tail=1000):
    return s if len(s) <= head + tail else s[:head] + "\n...[truncated]...\n" + s[-tail:]


class PodmanExecutor:
    def __init__(self, workdir, image, name):
        self.workdir, self.image, self.name = Path(workdir).resolve(), image, name

    def start(self):
        subprocess.run([
            "podman", "run", "-d", "--rm", "--name", self.name,
            "--network", "none", "--userns=keep-id",
            "--memory=2g", "--pids-limit=256",
            "-v", f"{self.workdir}:/workspace", "-w", "/workspace",
            self.image, "sleep", "infinity",
        ], check=True, capture_output=True, text=True)

    def run(self, cmd, timeout):
        try:
            p = subprocess.run(
                ["podman", "exec", "-w", "/workspace", self.name,
                 "timeout", "-s", "KILL", str(timeout), "bash", "-c", cmd],
                capture_output=True, text=True, timeout=timeout + 15)
            return trunc(p.stdout + p.stderr), p.returncode
        except subprocess.TimeoutExpired:
            return "[command timed out]", 124

    def close(self):
        subprocess.run(["podman", "rm", "-f", self.name], capture_output=True)


class LocalExecutor:
    """NOT a sandbox. Only for dry runs with the scripted fake model."""

    def __init__(self, workdir):
        self.workdir = Path(workdir).resolve()

    def start(self):
        pass

    def run(self, cmd, timeout):
        try:
            p = subprocess.run(["bash", "-c", cmd], cwd=self.workdir,
                               capture_output=True, text=True, timeout=timeout)
            return trunc(p.stdout + p.stderr), p.returncode
        except subprocess.TimeoutExpired:
            return "[command timed out]", 124

    def close(self):
        pass


def make_executor(kind, workdir, image, name):
    return PodmanExecutor(workdir, image, name) if kind == "podman" else LocalExecutor(workdir)


# ---------- grading ----------

def parse_answer(s):
    s = s.strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s.lower().startswith("json"):
            s = s[4:]
    return json.loads(s)


def grade(puzzle, answer_str):
    """Returns (status, message). status: correct | incorrect | format."""
    try:
        obj = parse_answer(answer_str)
    except Exception as e:  # noqa: BLE001
        return "format", f"Invalid JSON ({e}). This did not use up a submission."
    sol = puzzle["solution"]
    if not isinstance(obj, dict) or set(obj) != set(sol):
        return "format", f"Keys must be exactly {sorted(sol)}. This did not use up a submission."
    n = puzzle["n_houses"]
    for k, v in obj.items():
        if not isinstance(v, list) or len(v) != n:
            return "format", f"'{k}' must be a list of {n} items. This did not use up a submission."
    norm = lambda lst: [str(x).strip().lower() for x in lst]  # noqa: E731
    ok = all(norm(obj[k]) == norm(sol[k]) for k in sol)
    return ("correct", "Correct.") if ok else ("incorrect", "Incorrect.")


# ---------- logging ----------

class RunLog:
    def __init__(self, path, base):
        self.f = open(path, "a", buffering=1, encoding="utf-8")
        self.base, self.turn = base, 0

    def log(self, event, **kw):
        rec = {"ts": time.time(), "event": event, "turn": self.turn, **self.base, **kw}
        self.f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def close(self):
        self.f.close()


# ---------- the agent loop ----------

# 4xx errors other than 408/409/429 will not get better by retrying.
NO_RETRY = {400, 401, 403, 404, 422}


def consume_stream(stream):
    """Read a Responses SSE stream and return the final Response object.

    Streaming keeps the connection busy, so the gateway's ~60 s limit on silent non-streaming
    requests (504 Gateway Time-out) no longer kills long reasoning generations."""
    final, items = None, []
    for ev in stream:
        t = getattr(ev, "type", None)
        if t in ("response.completed", "response.incomplete", "response.failed"):
            final = getattr(ev, "response", None)
        elif t == "response.output_item.done":
            items.append(getattr(ev, "item", None))
        elif t == "error":
            raise RuntimeError(f"stream error event: {plain(ev)}")
    if final is None:
        raise RuntimeError("stream ended without a response.completed event")
    if not getattr(final, "output", None) and items:
        try:
            final.output = [i for i in items if i is not None]
        except Exception:  # noqa: BLE001
            pass
    return final


def call_model(client, cfg, system, history, log):
    """One Responses API call. The SDK already retries 408/409/429/5xx and connection errors
    (max_retries on the client), so this outer loop is short and skips non-retryable 4xx."""
    kw = dict(model=cfg["model"], instructions=system, input=history, tools=cfg.get("tools") or TOOLS,
              tool_choice="auto", temperature=cfg["temperature"],
              max_output_tokens=cfg["max_tokens_per_call"])
    if cfg.get("extra_body"):
        kw["extra_body"] = cfg["extra_body"]
    if cfg.get("stream"):
        kw["stream"] = True
    for attempt in range(3):
        try:
            resp = client.responses.create(**kw)
            if cfg.get("stream"):
                resp = consume_stream(resp)
            if getattr(resp, "status", "completed") == "failed":
                err = plain(getattr(resp, "error", None))
                log.log("api_error", attempt=attempt, error=f"response failed: {err}")
                time.sleep(min(60, 2 ** attempt * 2))
                continue
            return resp
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "status_code", None)
            log.log("api_error", attempt=attempt, status_code=code, error=repr(e))
            if code in NO_RETRY:
                return None
            time.sleep(min(60, 2 ** attempt * 2))
    return None


def safe_path(work, p):
    p = p.replace("/workspace/", "", 1) if p.startswith("/workspace/") else p
    full = (work / p).resolve()
    if work.resolve() not in full.parents and full != work.resolve():
        raise ValueError("path outside /workspace")
    return full


def reasoning_text(items):
    """Readable reasoning from `reasoning` items. On Adaption/qwen3.5 the raw text arrives in
    `content` (summary is empty, no encrypted_content). Handles list-of-parts or plain string."""
    out = []
    for it in items:
        if it.get("type") != "reasoning":
            continue
        for field in ("summary", "content"):
            v = it.get(field)
            if isinstance(v, str):
                out.append(v)
            elif isinstance(v, list):
                for part in v:
                    if isinstance(part, str):
                        out.append(part)
                    elif isinstance(part, dict) and part.get("text"):
                        out.append(part["text"])
    return "\n".join(out) or None


def run_agent(puzzle, run_id, client, acct, cfg, budget, out_dir,
              executor_kind="podman", image="python:3.12-slim", git_hash="none"):
    run_dir = Path(out_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    work = run_dir / "workspace"
    work.mkdir(exist_ok=True)
    (work / "puzzle.txt").write_text(puzzle["prompt"])

    log = RunLog(run_dir / "events.jsonl", {"run_id": run_id, "puzzle_id": puzzle["id"], "account": acct})
    ex = make_executor(executor_kind, work, image, f"pilot_{run_id}_{uuid.uuid4().hex[:6]}")
    t0 = time.time()
    st = dict(ptok=0, ctok=0, cached=0, rtok=0, cost=0.0, submissions=0, format_errors=0, solved=False,
              first_correct_submission=None, stop="max_turns", turns=0, nudges=0,
              incomplete=0, last_ctx=0)

    system = SYSTEM_PROMPT.format(max_submissions=cfg["max_submissions"])
    log.log("run_start", model=cfg["model"], temperature=cfg["temperature"], git_hash=git_hash,
            executor=executor_kind, image=image, api="adaption.responses", system=system,
            limits={k: cfg[k] for k in (
                "max_turns", "max_tokens_per_call", "max_total_tokens", "max_wall_s",
                "max_submissions", "context_limit")}, replay_reasoning=cfg.get("replay_reasoning", True), stream=bool(cfg.get("stream")))
    history = [{"role": "user", "content": puzzle["prompt"]}]
    logged_upto = 0
    try:
        ex.start()
        for turn in range(1, cfg["max_turns"] + 1):
            log.turn = st["turns"] = turn
            if time.time() - t0 > cfg["max_wall_s"]:
                st["stop"] = "wall_clock"; break
            if st["ptok"] + st["ctok"] >= cfg["max_total_tokens"]:
                st["stop"] = "token_cap"; break
            if not budget.can_spend(acct):
                st["stop"] = "budget_stop"; break
            # the model's context (input + output) is limited; stop cleanly instead of getting a 400
            if st["last_ctx"] + 2000 + cfg["max_tokens_per_call"] > cfg["context_limit"]:
                st["stop"] = "context_cap"; break

            new_items, logged_upto = history[logged_upto:], len(history)
            resp = call_model(client, cfg, system, history, log)
            if resp is None:
                st["stop"] = "api_error"; break

            u = plain(getattr(resp, "usage", None)) or {}
            ptok, ctok = as_int(u.get("input_tokens")), as_int(u.get("output_tokens"))
            cached = as_int((u.get("input_tokens_details") or {}).get("cached_tokens"))
            rtok = as_int((u.get("output_tokens_details") or {}).get("reasoning_tokens"))
            st["ptok"] += ptok; st["ctok"] += ctok; st["cached"] += cached; st["rtok"] += rtok
            st["last_ctx"] = ptok + ctok
            reported = u.get("cost")
            st["cost"] += budget.add(acct, ptok, ctok, cached, reported)

            items = plain(list(resp.output or []))
            status = getattr(resp, "status", None)
            incomplete = (plain(getattr(resp, "incomplete_details", None)) or {}).get("reason")
            if status == "incomplete" or incomplete:
                st["incomplete"] += 1
            log.log("model_response", new_items=new_items, output=items, raw=plain(resp),
                    prompt_tokens=ptok, completion_tokens=ctok, cached_tokens=cached,
                    reasoning_tokens=rtok, reported_cost=reported, status=status, incomplete_reason=incomplete,
                    reasoning=reasoning_text(items), response_id=getattr(resp, "id", None),
                    model_reported=getattr(resp, "model", None))

            # replay what the model produced; optionally drop reasoning items (Qwen advises not
            # keeping earlier-turn thinking; it also shrinks context and cost). Full items stay logged.
            history.extend(i for i in items if cfg.get("replay_reasoning", True) or i.get("type") != "reasoning")
            calls = [it for it in items if it.get("type") == "function_call"]

            if not calls:
                st["nudges"] += 1
                if st["nudges"] > 3:
                    st["stop"] = "no_tool_call"; break
                history.append({"role": "user", "content":
                                "No tool call received. Keep working, or call submit_answer with your answer."})
                continue
            st["nudges"] = 0

            done = False
            for tc in calls:
                name = tc.get("name")
                call_id = tc.get("call_id")
                raw_args = tc.get("arguments") or "{}"
                if done:
                    result = "The run has already finished."
                else:
                    try:
                        args = json.loads(raw_args)
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be a JSON object")
                    except Exception as e:  # noqa: BLE001
                        args, result = None, f"Error: arguments were not valid JSON ({e})"
                    if args is not None:
                        try:
                            if name == "bash":
                                out, code = ex.run(args.get("command", ""), cfg["bash_timeout"])
                                result = f"{out}\n[exit code {code}]"
                            elif name == "read_file":
                                result = trunc(safe_path(work, args["path"]).read_text(errors="replace"))
                            elif name == "write_file":
                                fp = safe_path(work, args["path"])
                                fp.parent.mkdir(parents=True, exist_ok=True)
                                fp.write_text(args["content"])
                                result = f"Wrote {len(args['content'])} characters to {args['path']}"
                            elif name == "submit_answer":
                                # parallel submit calls in one turn must not bypass the submission limit
                                if st["submissions"] >= cfg["max_submissions"]:
                                    result = "No submissions left."
                                else:
                                    status_g, result = grade(puzzle, args.get("answer", ""))
                                    if status_g == "format":
                                        st["format_errors"] += 1
                                        if st["format_errors"] >= 5:
                                            st["stop"] = "format_errors"; done = True
                                    else:
                                        st["submissions"] += 1
                                        log.log("submission", status=status_g, n=st["submissions"],
                                                answer=args.get("answer", ""))
                                        if status_g == "correct":
                                            st["solved"] = True
                                            st["first_correct_submission"] = st["submissions"]
                                            st["stop"] = "solved"; done = True
                                        elif st["submissions"] >= cfg["max_submissions"]:
                                            st["stop"] = "out_of_submissions"; done = True
                                            result += " No submissions left."
                            else:
                                result = f"Error: unknown tool {name}"
                        except Exception as e:  # noqa: BLE001
                            result = f"Error: {e}"
                log.log("tool_call", name=name, call_id=call_id, arguments=raw_args, result=result)
                history.append({"type": "function_call_output", "call_id": call_id, "output": result})
            if done:
                break
    finally:
        ex.close()
        summary = {
            "run_id": run_id, "puzzle_id": puzzle["id"], "size": f"{puzzle['n_houses']}x{puzzle['n_cats']}",
            "n_clues": puzzle["n_clues"], "account": acct, "model": cfg["model"],
            "solved": st["solved"], "stop_reason": st["stop"], "turns": st["turns"],
            "submissions": st["submissions"], "format_errors": st["format_errors"],
            "first_correct_submission": st["first_correct_submission"],
            "prompt_tokens": st["ptok"], "completion_tokens": st["ctok"],
            "cached_tokens": st["cached"], "reasoning_tokens": st["rtok"],
            "incomplete_calls": st["incomplete"], "max_context_seen": st["last_ctx"],
            "total_tokens": st["ptok"] + st["ctok"], "est_cost_usd": round(st["cost"], 5),
            "wall_s": round(time.time() - t0, 1), "git_hash": git_hash,
        }
        log.log("run_end", **summary)
        log.close()
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary
