"""A scripted stand-in for the Adaption Responses API, used only to test the harness offline.

Script: run `ls`, write a file, submit a wrong answer, then submit the right one
(plus a stray parallel extra submit, to check the harness ignores it).
Output items mimic the real shape: reasoning, function_call(call_id, name, arguments), message.
"""
import json


def _dump(v):
    if hasattr(v, "model_dump"):
        return v.model_dump()
    if isinstance(v, list):
        return [_dump(x) for x in v]
    if isinstance(v, dict):
        return {k: _dump(x) for k, x in v.items()}
    return v


class _N:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self, **_):
        return {k: _dump(v) for k, v in self.__dict__.items()}


class _Responses:
    def __init__(self, puzzle):
        self.puzzle = puzzle

    def create(self, **kw):
        hist = kw["input"]
        n = sum(1 for m in hist if isinstance(m, dict) and m.get("type") == "function_call")
        sol = self.puzzle["solution"]
        wrong = {k: v[1:] + v[:1] for k, v in sol.items()}
        calls = []
        if n == 0:
            calls = [("bash", {"command": "ls; python3 --version"})]
        elif n == 1:
            calls = [("write_file", {"path": "notes.txt", "content": "scratch notes"})]
        elif n == 2:
            calls = [("submit_answer", {"answer": json.dumps(wrong)})]
        elif n == 3:
            calls = [("submit_answer", {"answer": json.dumps(sol)}),
                     ("submit_answer", {"answer": json.dumps(wrong)})]
        out = [_N(type="reasoning", id=f"rs_{n}", summary=[{"type": "summary_text", "text": "(fake reasoning)"}])]
        for i, (name, args) in enumerate(calls):
            out.append(_N(type="function_call", id=f"fc_{n}_{i}", call_id=f"call_{n}_{i}",
                          name=name, arguments=json.dumps(args), status="completed"))
        if not calls:
            out.append(_N(type="message", id=f"msg_{n}", role="assistant", status="completed",
                          content=[{"type": "output_text", "text": "done"}]))
        ptok = sum(len(str(m)) for m in hist) // 4
        usage = _N(input_tokens=ptok, output_tokens=40, total_tokens=ptok + 40,
                   input_tokens_details={"cached_tokens": ptok // 2},
                   output_tokens_details={"reasoning_tokens": 15})
        resp = _N(id=f"resp_{n}", object="response", status="completed", model="fake",
                  output=out, output_text="" if calls else "done", usage=usage,
                  error=None, incomplete_details=None)
        if kw.get("stream"):
            return iter([_N(type="response.created", response=resp),
                         _N(type="response.output_text.delta", delta=""),
                         _N(type="response.completed", response=resp)])
        return resp


class FakeClient:
    def __init__(self, puzzle):
        self.responses = _Responses(puzzle)
