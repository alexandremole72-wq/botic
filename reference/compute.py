"""Compute every derived BOTic figure from api/botic_facts.json and write reference/derived.json."""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))
from scoring import load_facts, summarize  # noqa: E402

facts = load_facts()
out = {k: summarize(r, facts) for k, r in facts["runs"].items() if not k.startswith("_")}
json.dump(out, open(os.path.join(ROOT, "reference", "derived.json"), "w"), indent=1, ensure_ascii=False)
for k, v in out.items():
    print(f"{k:20} traps {v['traps_avg']:>4}/9  lost €{v['euros_lost']:>5,}  crit {v['critical']}  viol {v['violations']:>2}  score {v['score']:>3}  {v['certificate']}")
