# Pilot A: single-agent ZebraLogic calibration

Finds which puzzle sizes the model solves 10-40% of the time, with no rules, honeypots or swarm.
The agent loop runs on your machine; tool calls run in a podman container with no network.
The real answer never enters the container.

## Files

| File | Purpose |
| --- | --- |
| `gen_puzzles.py` | Generates puzzles with a z3-verified unique solution |
| `harness.py` | Agent loop, tools, grader, budget guard, logging |
| `run_pilot.py` | Runs the sweep (resumable, parallel, two accounts) |
| `analyze.py` | Solve rate with 95% intervals, tokens, cost per size |
| `smoke_api.py` | Per account: model list, tool-call round trip, `usage`, reasoning item, rate-limit headers, optional parallel burst |
| `fake_model.py` | Scripted stand-in for the API, for offline tests |

## 1. Setup on Arch

```bash
sudo pacman -S --needed python uv git podman
grep $USER /etc/subuid /etc/subgid          # must show entries (rootless podman)
podman pull docker.io/library/python:3.12-slim
git init && git add . && git commit -m init  # so every log carries a git hash
uv sync                                      # installs openai and z3-solver
```

## 2. Check the API (costs a few cents)

Adaption serves the **Responses API** through its own `adaption` SDK (`client.responses.create`),
not OpenAI chat completions. The SDK knows the endpoint, so you only need keys:

```bash
export ADAPTION_API_KEY_1=pt_live_...   # account 1
export ADAPTION_API_KEY_2=pt_live_...   # account 2 (optional)
export ADAPTION_MODEL=qwen3.5-397b
# do NOT set ADAPTION_BASE_URL: the SDK reads it and would redirect every call
uv run python smoke_api.py              # add --burst 8 to probe parallel limits / 429s
```

Look for: `requested model available: True`, a `function_call` item came back and the round trip
worked, `usage` has `input_tokens`/`output_tokens` (and `cached_tokens`), and what the `reasoning`
item contains. If only a short `summary` (or just `encrypted_content`) appears, you do NOT get raw
chain-of-thought, which matters for the intent labelling in the design document.
If the endpoint needs extra request fields, put them in `ADAPTION_EXTRA_BODY='{"...": ...}'`.

Prices on the models page are in **credits per 1M tokens** (qwen3.5-397b: 0.55 in, 0.225 cached in,
3.50 out). The defaults match; confirm the credit-to-dollar rate on your dashboard:

```bash
export PRICE_IN=0.55 PRICE_CACHED=0.225 PRICE_OUT=3.50
```

## 3. Check the sandbox without spending anything

```bash
uv run python gen_puzzles.py --sizes 4x4 --per-size 2 --out puzzles_test
uv run python run_pilot.py --puzzles puzzles_test --fake --executor podman --runs-per-size 1 --out runs/fake_podman
```

This runs the scripted fake model, but its `ls` and `python3 --version` calls execute inside a real
container. Check `runs/fake_podman/*/events.jsonl`. If podman refuses `--memory` or `--pids-limit`,
cgroup delegation is missing; remove those two flags in `PodmanExecutor.start` in `harness.py`.

Leftover containers after a crash: `podman ps -a --filter name=pilot_ -q | xargs -r podman rm -f`

## 4. Generate the puzzles

```bash
uv run python gen_puzzles.py --sizes 4x4 5x4 5x5 6x5 6x6 --per-size 20 --out puzzles
```

Seconds in total. Sizes are HOUSESxATTRIBUTES, up to 6x6. Add smaller or larger steps if the first
results show everything solved or nothing solved.

## 5. Run in stages (cheapest first)

```bash
# a. two real runs, short, to see real tokens and behaviour
uv run python run_pilot.py --limit 2 --max-turns 8 --out runs/smoke --cap-per-account 5

# b. about 5 runs per size to measure tokens per run
uv run python run_pilot.py --runs-per-size 5 --out runs/pilot_a --cap-per-account 100
uv run python analyze.py runs/pilot_a

# c. the full pilot, using the same output folder (finished runs are skipped)
uv run python run_pilot.py --runs-per-size 20 --workers 4 --out runs/pilot_a --cap-per-account 150
uv run python analyze.py runs/pilot_a
```

Runs alternate between the two accounts. `--cap-per-account` stops spending on an account at that
estimated amount, and the count survives restarts (`runs/<out>/budget.json`). The estimate comes from
token usage times your prices, so compare it with both provider dashboards after stage a.

## 6. Read the results

`analyze.py` prints one row per size: solve rate with a 95% interval, an `in band` flag for 10-40%,
tokens and cost percentiles, stop reasons and per-puzzle counts. It also writes `analysis.csv`.

Pick easy, medium and hard from the sizes whose rate falls in or near the band. Then run **swarms
with no shortcuts** on those 2-3 sizes (5 to 10 swarms each) and set the final difficulty on the
swarm solve rate, as discussed.

Useful checks in the raw logs:

```bash
grep -l '"incomplete_reason": "max_output_tokens"' runs/pilot_a/*/events.jsonl | wc -l   # calls cut off by max tokens
grep -h '"event": "api_error"' runs/pilot_a/*/events.jsonl | head          # API problems
```

If many calls end `incomplete` (`incomplete_calls` in summary.json), raise `--max-tokens-per-call` (the default 8192 may be small for a
reasoning model). If many end by `no_tool_call`, look at what the model wrote instead.

## Design decisions to know about

- **No z3 in the container.** The agent has plain Python 3 and no internet, so it can write its own
  search or solver. If it solves everything, use bigger grids; the pilot will show it.
- **Feedback.** Up to 3 submissions, pass or fail only. A malformed answer does not use up a
  submission (5 malformed answers end the run).
- **Prompt.** One agent, no team, board or honeypot text. The swarm prompt will differ, which is why
  difficulty is confirmed with swarms before the main run.
- **Logging.** `events.jsonl` per run holds the raw response, token usage, reasoning text if the API
  returns it, tool calls and results. Requests are stored as the messages added since the previous
  call, so the full history can be rebuilt without storing it on every turn.
- **Caps** (all flags on `run_pilot.py`): 40 turns, 1.5M total tokens, 20 minutes, 30 s per command,
  and a context guard (`--context-limit`, default 131072): a run stops with `context_cap` instead of
  hitting a 400. The full history, including `reasoning` items, is replayed every turn as the Adaption
  docs show, so watch `max_context_seen` in summary.json.
- **Submissions.** Parallel `submit_answer` calls in one turn cannot exceed `--max-submissions`.
- **English only** for now. The structured clues in each puzzle file are language-neutral; Ukrainian
  needs a second rendering function next to `render_prompt_en`, reviewed by a native speaker.
- **Local executor** (`--executor local`) is not a sandbox. It exists only for the fake-model test.
