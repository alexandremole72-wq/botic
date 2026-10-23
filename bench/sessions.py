"""Live runs driven from outside: an agent connected over MCP, or a browser
agent (or a person) using the web storefronts. A session holds one sandbox
world in memory; when the agent calls finish or escalate_to_human, or someone
clicks "Judge this run", the run is judged and stored like any other."""

from __future__ import annotations

import random
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .agents import AgentResult
from .runner import make_record
from .scenarios import Scenario, all_scenarios, get_scenario
from .storage import RunStore
from .world import World

SESSION_TTL_SECONDS = 3 * 3600
MAX_SESSIONS = 500


@dataclass
class ExternalAgent:
    """Stands in for the agent in the run record."""

    label: str
    provider: str = "external"
    model: str = ""
    policy: str = "external"

    def __post_init__(self) -> None:
        self.model = self.model or self.label


@dataclass
class Session:
    token: str
    scenario: Scenario
    world: World
    agent: ExternalAgent
    source: str  # mcp | web
    created_at: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock)
    record_id: Optional[str] = None
    web_carts: Dict[str, str] = field(default_factory=dict)  # store id -> open cart id (web door)
    web_views: Dict[str, Any] = field(default_factory=dict)  # checkout session id -> last view shown

    @property
    def judged(self) -> bool:
        return self.record_id is not None

    def call(self, name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self.lock:
            return self.world.call(name, {k: v for k, v in (args or {}).items() if v is not None})


class SessionRegistry:
    def __init__(self, store_factory: Callable[[], RunStore]):
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.Lock()
        self._store_factory = store_factory

    def create(self, scenario_id: str, agent_label: str, source: str) -> Session:
        if scenario_id in ("", "random", None):
            scenario = random.choice(all_scenarios())
        else:
            scenario = get_scenario(scenario_id)
        world = scenario.build()
        world.note("task", scenario.task)
        label = (agent_label or "").strip()[:80] or ("Browser session" if source == "web" else "MCP agent")
        session = Session(secrets.token_urlsafe(12), scenario, world, ExternalAgent(label), source)
        with self._lock:
            self._expire()
            if len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions.values(), key=lambda s: s.created_at)
                self._sessions.pop(oldest.token, None)
            self._sessions[session.token] = session
        return session

    def get(self, token: Optional[str]) -> Optional[Session]:
        if not token:
            return None
        with self._lock:
            return self._sessions.get(token)

    def list(self) -> List[Session]:
        with self._lock:
            return sorted(self._sessions.values(), key=lambda s: -s.created_at)

    def finalize(self, session: Session) -> str:
        """Judge and store the run once; later calls return the same record id."""
        with session.lock:
            if session.record_id:
                return session.record_id
            if not session.world.done:
                session.world.end("stopped")
            record = make_record(session.agent, session.scenario, session.world, AgentResult(), None,
                                 session.source, int((time.time() - session.created_at) * 1000))
            self._store_factory().save(record)
            session.record_id = record["id"]
            return record["id"]

    def _expire(self) -> None:
        now = time.time()
        for token in [t for t, s in self._sessions.items() if now - s.created_at > SESSION_TTL_SECONDS]:
            self._sessions.pop(token, None)
