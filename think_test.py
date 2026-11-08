import os, json
from adaption import Adaption
c = Adaption(api_key=os.environ["ADAPTION_API_KEY_1"], timeout=300.0)
prompt = "Alice, Bob and Carol live in houses 1-3. Alice is left of Bob. Carol is in house 1. Who is in house 2? Think it through."
variants = {
    "none": {},
    "reasoning.effort=high": {"reasoning": {"effort": "high"}},
    "reasoning.effort=medium": {"reasoning": {"effort": "medium"}},
    "enable_thinking": {"chat_template_kwargs": {"enable_thinking": True}},
}

def dump(x):
    return x.model_dump(exclude_unset=True, mode="json") if hasattr(x, "model_dump") else x

for name, body in variants.items():
    try:
        r = c.responses.create(model="gemma-4-26b-a4b-it", input=prompt, max_output_tokens=4000, extra_body=body or None)
        u = dump(r.usage) or {}
        d = u.get("output_tokens_details") or {}
        rt = d.get("reasoning_tokens") if isinstance(d, dict) else getattr(d, "reasoning_tokens", None)
        items = [o.type for o in r.output]
        txt = []
        for o in r.output:
            if o.type == "reasoning":
                od = dump(o)
                txt.append(str(od.get("content") or od.get("summary") or "")[:150])
        print(f"{name:26} out_tokens={u.get('output_tokens')} reasoning_tokens={rt} items={items} text={txt}")
    except Exception as e:
        print(f"{name:26} ERROR {repr(e)[:300]}")
