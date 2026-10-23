"""Command line: python -m bench <command>

  scenarios                 list the 9 missions and their 27 variants
  run --agents A,B,...      run agents on the scenarios, judge and store every run
  report [--out DIR]        print the leaderboard; write leaderboard.md, results.json, runs.csv
  show RUN_ID               replay one run in the terminal
  export-site [--out DIR]   the dashboard and every replay as static HTML, to publish anywhere
  serve [--port 8000]       dashboard, replays, MCP door and web storefronts
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional


def _split(value: Optional[str]) -> Optional[List[str]]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def cmd_scenarios(args: argparse.Namespace) -> None:
    from .scenarios import MISSIONS, TRAPS

    for m in MISSIONS:
        print(f"{m.id}  {TRAPS[m.trap]} - {m.title}")
        print(f"    Task: {m.task}")
        for i, v in enumerate(m.variants, 1):
            print(f"    {m.id}.{i} [{v.language}] {v.note}")
        print()


def cmd_run(args: argparse.Namespace) -> None:
    from .report import summarize, terminal_table
    from .runner import run_batch
    from .scenarios import all_scenarios
    from .storage import RunStore

    agents = _split(args.agents) or []
    if not agents:
        sys.exit("Give at least one agent, e.g. --agents naive,careful or --agents anthropic:claude-sonnet-5-5")
    policies = ["baseline", "policy"] if args.policy == "both" else [args.policy]
    scenarios = all_scenarios(_split(args.missions), [int(v) for v in _split(args.variants) or []] or None)
    store = RunStore(args.db)
    total = len(agents) * len(policies) * len(scenarios) * args.repeats
    print(f"Running {total} runs ({len(agents)} agent(s) x {len(policies)} policy x {len(scenarios)} scenarios x "
          f"{args.repeats} repeat(s)). Database: {store.url.split('@')[-1]}")
    done = {"n": 0}

    def progress(rec: dict) -> None:
        done["n"] += 1
        money = f" €{rec['money_at_risk_cents'] / 100:.2f} at risk" if rec["money_at_risk_cents"] else ""
        line = rec["summary"] or ""
        print(f"[{done['n']:>3}/{total}] {rec['agent'][:40]:40} {rec['scenario_id']:5} {rec['outcome']:8}{money}"
              f"  {line[:110]}", flush=True)

    out = run_batch(agents, scenarios, policies, args.repeats, store, args.workers, args.effort, progress,
                    retries=args.retries)
    print(f"\nBatch {out['batch_id']} stored.\n")
    print(terminal_table(summarize(out["records"])))


def cmd_report(args: argparse.Namespace) -> None:
    from .report import markdown, results_json, runs_csv, summarize, terminal_table
    from .storage import RunStore

    store = RunStore(args.db)
    records = store.list(limit=100_000, batch_id=args.batch, include_scripted=not args.models_only)
    if not records:
        sys.exit("No runs stored yet. Start with: python -m bench run --agents naive,careful")
    rows = summarize(records)
    print(terminal_table(rows))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "leaderboard.md"), "w", encoding="utf-8") as f:
            f.write(markdown(rows))
        with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
            f.write(results_json(rows, records))
        with open(os.path.join(args.out, "runs.csv"), "w", encoding="utf-8", newline="") as f:
            f.write(runs_csv(records))
        print(f"\nWrote leaderboard.md, results.json and runs.csv to {args.out}")


def cmd_show(args: argparse.Namespace) -> None:
    from .storage import RunStore

    rec = RunStore(args.db).get(args.run_id)
    if not rec:
        sys.exit(f"No run {args.run_id}")
    print(f"{rec['agent']} on {rec['scenario_id']} ({rec['trap']}): {rec['outcome'].upper()}")
    print(f"Task: {rec['task']}\n")
    for e in rec["events"] or []:
        if e["kind"] == "tool_call":
            flags = f"  [{', '.join(e['flags'])}]" if e["flags"] else ""
            print(f"{e['step']:>3}. {e['name']}({json.dumps(e['args'], ensure_ascii=False)[:120]}){flags}")
            print(f"     -> {json.dumps(e['result'], ensure_ascii=False)[:200]}")
        elif e["kind"] in ("agent_message", "end"):
            print(f"{e['step']:>3}. [{e['kind']}] {str(e['result'] or e['name'])[:300]}")
    v = rec["verdict"] or {}
    print(f"\nVerdict: {rec['outcome']} - {rec['summary']}")
    if v:
        print(f"Expected: {v['expected']}")
        if v["rules_broken"]:
            print(f"Rules broken: {', '.join(v['rules_broken'])}; money at risk €{v['money_at_risk_eur']:.2f}")


def cmd_export_site(args: argparse.Namespace) -> None:
    from .export import export_site

    n = export_site(args.out, args.db, args.models_only)
    print(f"Wrote {n} pages to {args.out}. Open {os.path.join(args.out, 'index.html')} or upload the folder.")


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    if args.db:
        os.environ["BOTIC_DB_URL"] = args.db
    uvicorn.run("bench.web:app", host=args.host, port=args.port, reload=False)


def main(argv: Optional[List[str]] = None) -> None:
    p = argparse.ArgumentParser(prog="python -m bench", description="BOTic Bench: tests whether an AI agent buys "
                                "right before it is allowed to pay.")
    p.add_argument("--db", help="database URL (default: BOTIC_DB_URL, DATABASE_URL or sqlite:///botic_bench.db)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("scenarios", help="list missions and variants").set_defaults(fn=cmd_scenarios)

    r = sub.add_parser("run", help="run agents on scenarios")
    r.add_argument("--agents", required=True,
                   help="comma-separated: naive, careful, anthropic:<model>, openai:<model>, gemini:<model>")
    r.add_argument("--policy", default="baseline", choices=["baseline", "policy", "both"],
                   help="system prompt: plain assistant, assistant with a purchasing policy, or both")
    r.add_argument("--missions", help="e.g. M1,M7 (default: all)")
    r.add_argument("--variants", help="e.g. 1,2 (default: all three)")
    r.add_argument("--repeats", type=int, default=1, help="runs per scenario, to measure consistency")
    r.add_argument("--workers", type=int, default=1, help="runs in parallel")
    r.add_argument("--effort", help="reasoning effort passed to the model (low, medium, high...)")
    r.add_argument("--retries", type=int, default=2, help="restart a run that crashed on an API error")
    r.set_defaults(fn=cmd_run)

    rep = sub.add_parser("report", help="leaderboard and exports")
    rep.add_argument("--out", help="folder for leaderboard.md, results.json, runs.csv")
    rep.add_argument("--batch", help="only this batch id")
    rep.add_argument("--models-only", action="store_true", help="leave out the scripted baselines")
    rep.set_defaults(fn=cmd_report)

    s = sub.add_parser("show", help="replay one run")
    s.add_argument("run_id")
    s.set_defaults(fn=cmd_show)

    ex = sub.add_parser("export-site", help="write the dashboard and every replay as static HTML")
    ex.add_argument("--out", default="site", help="output folder (default: site)")
    ex.add_argument("--models-only", action="store_true", help="leave out the scripted baselines")
    ex.set_defaults(fn=cmd_export_site)

    sv = sub.add_parser("serve", help="start the web app")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
