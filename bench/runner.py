"""Run agents on scenarios, judge each run and store the record."""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from .agents import AgentResult, make_agent
from .judge import judge
from .scenarios import Scenario
from .storage import RunStore
from .world import World


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def make_record(agent: Any, scenario: Scenario, world: World, result: AgentResult, batch_id: Optional[str],
                source: str, duration_ms: int) -> Dict[str, Any]:
    # The run is judged even when it crashed, so the replay shows what happened,
    # but a crashed run is recorded as "error" and left out of every score.
    verdict = judge(scenario, world)
    failed = bool(result.error)
    return {
        "id": new_id(),
        "batch_id": batch_id,
        "created_at": datetime.now(timezone.utc),
        "agent": agent.label,
        "provider": agent.provider,
        "model": agent.model,
        "policy": agent.policy,
        "source": source,
        "scenario_id": scenario.id,
        "mission_id": scenario.mission.id,
        "variant": scenario.variant,
        "trap": scenario.trap,
        "outcome": "error" if failed else verdict.outcome,
        "passed": False if failed else verdict.passed,
        "money_at_risk_cents": 0 if failed else verdict.money_at_risk,
        "spent_cents": sum(o["total_cents"] for o in world.orders),
        "tool_calls": sum(1 for e in world.events if e.kind == "tool_call"),
        "model_calls": result.model_calls,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "duration_ms": duration_ms,
        "task": scenario.task,
        "summary": f"Run error: {result.error}" if failed else verdict.summary,
        "final_report": world.final_report,
        "error": result.error,
        "verdict": verdict.to_dict(),
        "events": [asdict(e) for e in world.events],
    }


def run_one(agent: Any, scenario: Scenario, store: Optional[RunStore] = None, batch_id: Optional[str] = None,
            source: str = "runner") -> Dict[str, Any]:
    world = scenario.build()
    world.note("task", scenario.task)
    started = time.perf_counter()
    hints = scenario.mission.hints if getattr(agent, "needs_hints", False) else None
    result = agent.run(world, scenario.task, hints)
    record = make_record(agent, scenario, world, result, batch_id, source,
                         int((time.perf_counter() - started) * 1000))
    if store is not None:
        store.save(record)
    return record


def run_batch(agent_specs: List[str], scenarios: Iterable[Scenario], policies: List[str], repeats: int = 1,
              store: Optional[RunStore] = None, workers: int = 1, effort: Optional[str] = None,
              on_record: Optional[Callable[[Dict[str, Any]], None]] = None, retries: int = 2) -> Dict[str, Any]:
    """Every agent x policy x scenario x repeat. A run that crashes (API error,
    rate limit) is started again from scratch, up to `retries` times; only the
    last attempt is stored. Returns the batch id and records."""
    batch_id = new_id()
    scenarios = list(scenarios)
    jobs = [(spec, policy, sc) for spec in agent_specs for policy in policies
            for sc in scenarios for _ in range(repeats)]
    records: List[Dict[str, Any]] = []

    def job(spec: str, policy: str, sc: Scenario) -> Dict[str, Any]:
        for attempt in range(retries + 1):
            rec = run_one(make_agent(spec, policy, effort), sc, None, batch_id)
            if rec["outcome"] != "error" or attempt == retries:
                break
            time.sleep(5 * (attempt + 1))
        if store is not None:
            store.save(rec)
        return rec

    if workers <= 1:
        for spec, policy, sc in jobs:
            rec = job(spec, policy, sc)
            records.append(rec)
            if on_record:
                on_record(rec)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(job, *j) for j in jobs]
            for fut in as_completed(futures):
                rec = fut.result()
                records.append(rec)
                if on_record:
                    on_record(rec)
    return {"batch_id": batch_id, "records": records}
