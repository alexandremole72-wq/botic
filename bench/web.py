"""BOTic Bench web app.

  /                     leaderboard
  /runs, /runs/{id}     every run and its step-by-step replay
  /missions             the nine missions and their variants
  /try                  start a live run for your own agent
  /mcp                  MCP door (Streamable HTTP), scoped by a run token
  /shop/{token}/...     web door: the trapped stores as web pages
  /api/...              JSON API (docs at /api/docs)

Start it with:  python -m bench serve   or   uvicorn bench.web:app
"""

from __future__ import annotations

import hashlib
import html
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.routing import Route

from . import __version__, catalog
from .agents.prompts import system_prompt
from .mcp_server import build_mcp_server, transport_security
from .present import OUTCOME_HELP, OUTCOME_LABELS, cents, describe, money, replay
from .report import summarize
from .scenarios import MISSIONS, TRAPS, all_scenarios
from .sessions import Session, SessionRegistry
from .storage import RunStore

HERE = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
templates.env.filters["money"] = money
templates.env.filters["cents"] = cents

TRAP_SHORT = {
    "prompt_injection": "Injection", "cloned_seller": "Look-alike", "fake_reviews": "Fakes", "hidden_fees": "Fees",
    "preselected_addon": "Add-on", "price_change": "Price", "changed_iban": "IBAN", "suspended_supplier": "Blocked",
    "impossible_task": "Impossible",
}
TRAP_COLUMNS = [{"id": m.trap, "short": TRAP_SHORT[m.trap], "long": TRAPS[m.trap], "mission": m} for m in MISSIONS]
SEVERITY = {"pass": 0, "safe": 1, "fail": 2, "critical": 3}

_store: Optional[RunStore] = None


def get_store() -> RunStore:
    global _store
    if _store is None:
        _store = RunStore()
    return _store


registry = SessionRegistry(get_store)
mcp = build_mcp_server(registry)


class _MCPEndpoint:
    """ASGI endpoint for /mcp. The MCP session manager can run only once, so a
    fresh one is created each time the app starts (tests start it many times)."""

    def __init__(self) -> None:
        self.handler: Any = None

    async def __call__(self, scope, receive, send) -> None:
        if self.handler is None:
            await JSONResponse({"error": "MCP server not started"}, status_code=503)(scope, receive, send)
            return
        await self.handler(scope, receive, send)


_mcp_endpoint = _MCPEndpoint()


@asynccontextmanager
async def lifespan(app: FastAPI):
    http_app = mcp.streamable_http_app(streamable_http_path="/mcp",
                                       transport_security=transport_security(os.environ.get("BOTIC_ALLOWED_HOSTS")))
    _mcp_endpoint.handler = http_app.routes[0].endpoint
    async with mcp.session_manager.run():
        yield
    _mcp_endpoint.handler = None


app = FastAPI(title="BOTic Bench", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
app.router.routes.append(Route("/mcp", endpoint=_mcp_endpoint))


def base_url(request: Request) -> str:
    return (os.environ.get("BOTIC_PUBLIC_URL") or str(request.base_url)).rstrip("/")


def common(request: Request, nav: str = "", **ctx: Any) -> Dict[str, Any]:
    # ?export=1 renders a page for the static site (python -m bench export-site): no forms, no live parts.
    return {"request": request, "nav": nav, "trap_names": TRAPS, "outcome_labels": OUTCOME_LABELS,
            "traps": TRAP_COLUMNS, "base": base_url(request), "static": request.query_params.get("export") == "1",
            **ctx}


# --------------------------------------------------------------------------- dashboard

def _cells(row: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    cells: Dict[str, List[Dict[str, str]]] = {}
    for col in TRAP_COLUMNS:
        m = col["mission"]
        out = []
        for i in range(1, len(m.variants) + 1):
            sid = f"{m.id}.{i}"
            outs = row["outcomes"].get(sid, [])
            if not outs:
                out.append({"cls": "", "title": f"{sid}: not run", "href": "/missions#" + m.id})
                continue
            worst = max(outs, key=lambda o: SEVERITY.get(o, 0))
            counts = ", ".join(f"{outs.count(o)} {OUTCOME_LABELS[o].lower()}" for o in SEVERITY if o in outs)
            href = (f"/runs/{row['latest_run'][sid]}" if len(outs) == 1 else
                    "/runs?" + urlencode({"agent": row["agent"], "trap": m.trap}))
            out.append({"cls": f"o-{worst}" + (" mixed" if len(set(outs)) > 1 else ""),
                        "title": f"{sid}: {counts}", "href": href, "latest": f"/runs/{row['latest_run'][sid]}"})
        cells[col["id"]] = out
    return cells


@app.get("/", response_class=HTMLResponse)
def leaderboard(request: Request):
    rows = summarize(get_store().list(limit=100_000))
    for r in rows:
        r["cells"] = _cells(r)
    return templates.TemplateResponse(request, "leaderboard.html", common(
        request, "board",
        model_rows=[r for r in rows if r["provider"] not in ("scripted", "external")],
        external_rows=[r for r in rows if r["provider"] == "external"],
        scripted_rows=[r for r in rows if r["provider"] == "scripted"]))


@app.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, agent: str = "", trap: str = "", outcome: str = ""):
    store = get_store()
    runs = store.list(limit=500, agent=agent or None, trap=trap or None, outcome=outcome or None)
    agents = sorted({r["agent"] for r in store.list(limit=100_000)})
    return templates.TemplateResponse(request, "runs.html", common(
        request, "runs", runs=runs, agents=agents, f={"agent": agent, "trap": trap, "outcome": outcome},
        outcomes=list(OUTCOME_LABELS.items())))


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: str):
    rec = get_store().get(run_id)
    if rec is None:
        raise HTTPException(404, "No such run")
    rp = replay(rec)
    return templates.TemplateResponse(request, "run.html", common(
        request, "runs", run=rec, verdict=rec.get("verdict"), steps=rp["steps"], mission=rp["mission"],
        variant_note=rp["variant_note"]))


@app.get("/missions", response_class=HTMLResponse)
def missions_page(request: Request):
    return templates.TemplateResponse(request, "missions.html", common(request, "missions", missions=MISSIONS))


@app.get("/try", response_class=HTMLResponse)
def try_page(request: Request):
    return templates.TemplateResponse(request, "try.html", common(
        request, "try", scenarios=all_scenarios(), live=registry.list()[:20]))


def _status(s: Session) -> Dict[str, Any]:
    calls = [e for e in s.world.events if e.kind == "tool_call"]
    if s.record_id:
        text = "Judged. Opening the replay."
    elif s.world.done:
        text = "The run has ended."
    elif calls:
        text = f"Running: {len(calls)} tool call{'s' if len(calls) > 1 else ''} so far."
    else:
        text = "Waiting for the agent's first call."
    last = describe(asdict(calls[-1]))["title"] if calls else ""
    return {"status": s.world.status, "status_text": text, "tool_calls": len(calls), "last_step": last,
            "run_id": s.record_id}


@app.post("/sessions")
async def create_session_form(request: Request):
    form = await request.form()
    door = "web" if form.get("door") == "web" else "mcp"
    try:
        s = registry.create(str(form.get("scenario") or "random"), str(form.get("agent") or ""), door)
    except KeyError as exc:
        raise HTTPException(400, str(exc))
    return RedirectResponse(f"/sessions/{s.token}", status_code=303)


@app.get("/sessions/{token}", response_class=HTMLResponse)
def session_page(request: Request, token: str):
    s = registry.get(token)
    if s is None:
        raise HTTPException(404, "This run does not exist or has expired")
    st = _status(s)
    return templates.TemplateResponse(request, "session.html", common(
        request, "try", s=s, status_text=st["status_text"], last_step=st["last_step"],
        system_prompt=system_prompt("baseline")))


@app.post("/sessions/{token}/judge")
def judge_session_form(token: str):
    s = registry.get(token)
    if s is None:
        raise HTTPException(404, "This run does not exist or has expired")
    return RedirectResponse(f"/runs/{registry.finalize(s)}", status_code=303)


# --------------------------------------------------------------------------- JSON API

class SessionIn(BaseModel):
    scenario: str = "random"
    agent: str = ""
    door: str = "mcp"


def _jsonable(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in rec.items()}


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/api/scenarios")
def api_scenarios():
    return [{"id": m.id, "trap": m.trap, "trap_label": TRAPS[m.trap], "title": m.title, "task": m.task,
             "expected": m.expected,
             "variants": [{"id": f"{m.id}.{i}", "language": v.language, "note": v.note}
                          for i, v in enumerate(m.variants, 1)]} for m in MISSIONS]


@app.get("/api/leaderboard")
def api_leaderboard():
    return summarize(get_store().list(limit=100_000))


@app.get("/api/runs")
def api_runs(agent: str = "", trap: str = "", outcome: str = "", limit: int = 200):
    return [_jsonable(r) for r in get_store().list(limit=min(limit, 5000), agent=agent or None,
                                                   trap=trap or None, outcome=outcome or None)]


@app.get("/api/runs/{run_id}")
def api_run(run_id: str):
    rec = get_store().get(run_id)
    if rec is None:
        raise HTTPException(404, "No such run")
    return _jsonable(rec)


@app.post("/api/sessions")
def api_create_session(body: SessionIn, request: Request):
    try:
        s = registry.create(body.scenario, body.agent, "web" if body.door == "web" else "mcp")
    except KeyError as exc:
        raise HTTPException(400, str(exc))
    base = base_url(request)
    return {"run": s.token, "scenario": s.scenario.id if body.scenario not in ("", "random") else "hidden",
            "task": s.scenario.task, "system_prompt": system_prompt("baseline"),
            "mcp_url": f"{base}/mcp?run={s.token}", "shop_url": f"{base}/shop/{s.token}/",
            "session_url": f"{base}/sessions/{s.token}"}


@app.get("/api/sessions/{token}")
def api_session(token: str):
    s = registry.get(token)
    if s is None:
        raise HTTPException(404, "This run does not exist or has expired")
    return _status(s)


@app.post("/api/sessions/{token}/judge")
def api_judge(token: str, request: Request):
    s = registry.get(token)
    if s is None:
        raise HTTPException(404, "This run does not exist or has expired")
    rec = get_store().get(registry.finalize(s))
    return {"run_id": rec["id"], "outcome": rec["outcome"], "passed": rec["passed"], "summary": rec["summary"],
            "money_at_risk_eur": (rec["money_at_risk_cents"] or 0) / 100, "verdict": rec["verdict"],
            "replay_url": f"{base_url(request)}/runs/{rec['id']}"}


# --------------------------------------------------------------------------- web door: the stores as pages

def _session_or_404(token: str) -> Session:
    s = registry.get(token)
    if s is None:
        raise HTTPException(404, "This shopping session does not exist or has expired")
    return s


def _shop(request: Request, s: Session, template: str, theme: str, **ctx: Any) -> HTMLResponse:
    world = s.world
    open_carts = [{"id": cid, "store": world.stores[sid].name, "count": sum(world.carts[cid]["lines"].values())}
                  for sid, cid in s.web_carts.items() if world.carts.get(cid, {}).get("status") == "open"]
    store = world.stores.get(theme)
    return templates.TemplateResponse(request, f"shop/{template}", {
        "request": request, "token": s.token, "s": s, "theme": theme, "store": store,
        "stores": list(world.stores.values()), "open_carts": open_carts, "root": f"/shop/{s.token}",
        "card_token": catalog.CARD_TOKEN, **ctx})


def _ended(request: Request, s: Session) -> Optional[HTMLResponse]:
    if not s.world.done:
        return None
    record_id = registry.finalize(s)
    return _shop(request, s, "ended.html", "workspace", record_id=record_id)


ICONS = {  # simple product drawings, centred on (240, 160)
    "mice": '<ellipse cx="240" cy="160" rx="62" ry="88" fill="{dark}"/><line x1="240" y1="72" x2="240" y2="140" '
            'stroke="{light}" stroke-width="6"/><rect x="232" y="100" width="16" height="30" rx="8" fill="{light}"/>',
    "chargers": '<rect x="180" y="110" width="120" height="110" rx="22" fill="{dark}"/><rect x="205" y="70" width="14" '
                'height="44" rx="4" fill="{dark}"/><rect x="261" y="70" width="14" height="44" rx="4" fill="{dark}"/>'
                '<rect x="226" y="180" width="28" height="10" rx="5" fill="{light}"/>',
    "storage": '<rect x="170" y="85" width="140" height="160" rx="26" fill="{dark}"/><rect x="190" y="105" width="100" '
               'height="120" rx="14" fill="none" stroke="{light}" stroke-width="5"/>',
    "monitors": '<rect x="135" y="70" width="210" height="130" rx="8" fill="{dark}"/><rect x="147" y="82" width="186" '
                'height="106" rx="3" fill="{light}"/><rect x="230" y="200" width="20" height="34" fill="{dark}"/>'
                '<rect x="195" y="232" width="90" height="10" rx="5" fill="{dark}"/>',
    "paper": '<rect x="160" y="150" width="160" height="34" rx="4" fill="{dark}"/><rect x="160" y="112" width="160" '
             'height="34" rx="4" fill="{dark}" opacity=".85"/><rect x="160" y="74" width="160" height="34" rx="4" '
             'fill="{dark}" opacity=".7"/><rect x="160" y="188" width="160" height="34" rx="4" fill="{dark}"/>',
    "ink": '<path d="M190 80h100v140a10 10 0 0 1-10 10h-80a10 10 0 0 1-10-10z" fill="{dark}"/><rect x="215" y="100" '
           'width="50" height="34" rx="4" fill="{light}"/><rect x="225" y="226" width="30" height="16" fill="{dark}"/>',
    "smart home": '<circle cx="240" cy="135" r="62" fill="{dark}"/><rect x="214" y="190" width="52" height="46" rx="6" '
                  'fill="{dark}"/><path d="M222 150q18-30 36 0" stroke="{light}" stroke-width="6" fill="none"/>',
    "keyboards": '<rect x="120" y="110" width="240" height="100" rx="12" fill="{dark}"/>'
                 + "".join(f'<rect x="{138 + 26 * i}" y="{128 + 24 * r}" width="18" height="14" rx="3" fill="{{light}}"/>'
                           for r in range(3) for i in range(8)),
    "cables": '<path d="M150 210c40-120 140 40 180-90" stroke="{dark}" stroke-width="14" fill="none" '
              'stroke-linecap="round"/><rect x="132" y="200" width="36" height="30" rx="6" fill="{dark}"/>'
              '<rect x="312" y="96" width="36" height="30" rx="6" fill="{dark}"/>',
    "webcams": '<rect x="160" y="90" width="160" height="80" rx="40" fill="{dark}"/><circle cx="240" cy="130" r="26" '
               'fill="{light}"/><circle cx="240" cy="130" r="12" fill="{dark}"/><rect x="215" y="170" width="50" '
               'height="50" rx="6" fill="{dark}"/>',
    "accessories": '<rect x="150" y="125" width="180" height="70" rx="16" fill="{dark}"/>'
                   + "".join(f'<rect x="{172 + 34 * i}" y="150" width="20" height="18" rx="3" fill="{{light}}"/>'
                             for i in range(5)),
    "services": '<path d="M240 70l80 30v60c0 50-38 80-80 95-42-15-80-45-80-95v-60z" fill="{dark}"/>'
                '<path d="M210 160l22 22 40-46" stroke="{light}" stroke-width="10" fill="none"/>',
    "filing": '<rect x="185" y="70" width="110" height="170" rx="6" fill="{dark}"/><circle cx="240" cy="190" r="18" '
              'fill="{light}"/><rect x="205" y="95" width="70" height="40" rx="4" fill="{light}"/>',
    "writing": '<rect x="228" y="60" width="24" height="170" rx="8" fill="{dark}" transform="rotate(30 240 150)"/>',
    "office": '<path d="M175 85h130v100l-30 30h-100z" fill="{dark}"/><path d="M305 185h-30v30z" fill="{light}"/>',
}


@app.get("/shop/{token}/img/{sku}.svg")
def shop_image(token: str, sku: str, i: int = 0):
    s = _session_or_404(token)
    p = s.world.products.get(sku)
    if p is None:
        raise HTTPException(404)
    hue = int(hashlib.md5(p.brand.encode()).hexdigest()[:2], 16) * 360 // 256
    dark, light = f"hsl({hue},28%,{38 + i * 6}%)", f"hsl({hue},40%,{88 - i * 4}%)"
    icon = ICONS.get(p.category, '<rect x="170" y="80" width="140" height="150" rx="14" fill="{dark}"/>')
    brand = html.escape(p.brand[:18])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="360" viewBox="0 0 480 360">'
           f'<rect width="480" height="360" fill="hsl({hue},35%,{93 - i * 3}%)"/>'
           + icon.replace("{dark}", dark).replace("{light}", light)
           + f'<text x="240" y="300" text-anchor="middle" font-family="Arial,sans-serif" font-size="26" '
             f'font-weight="700" fill="hsl({hue},40%,25%)">{brand}</text></svg>')
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "max-age=3600"})


@app.get("/shop/{token}/", response_class=HTMLResponse)
def shop_home(request: Request, token: str):
    s = _session_or_404(token)
    return _ended(request, s) or _shop(request, s, "home.html", "workspace")


@app.get("/shop/{token}/store/{store_id}", response_class=HTMLResponse)
def shop_store(request: Request, token: str, store_id: str):
    s = _session_or_404(token)
    if store_id not in s.world.stores:
        raise HTTPException(404, "No such store")
    cats = sorted({p.category for p in s.world.products.values() if p.store_id == store_id})
    return _ended(request, s) or _shop(request, s, "store.html", store_id, categories=cats)


@app.get("/shop/{token}/search", response_class=HTMLResponse)
def shop_search(request: Request, token: str, q: str = "", store: str = ""):
    s = _session_or_404(token)
    ended = _ended(request, s)
    if ended:
        return ended
    res: Dict[str, Any] = {}
    if q.strip():
        res = s.call("search_catalog", {"query": q, "store": store or None, "max_results": 20})
    sku_store = {sku: p.store_id for sku, p in s.world.products.items()}
    theme = store if store in s.world.stores else "search"
    return _shop(request, s, "search.html", theme, q=q, store_filter=store, res=res, sku_store=sku_store)


@app.get("/shop/{token}/p/{sku}", response_class=HTMLResponse)
def shop_product(request: Request, token: str, sku: str, error: str = ""):
    s = _session_or_404(token)
    ended = _ended(request, s)
    if ended:
        return ended
    p = s.world.products.get(sku)
    if p is None:
        raise HTTPException(404, "No such product")
    res = s.call("get_product", {"sku": sku})
    return _shop(request, s, "product.html", p.store_id, p=res, error=error)


@app.post("/shop/{token}/cart/add")
async def shop_cart_add(request: Request, token: str):
    s = _session_or_404(token)
    form = await request.form()
    sku = str(form.get("sku", ""))
    raw_qty = str(form.get("quantity") or "1").strip()
    qty = max(1, int(raw_qty)) if raw_qty.isdigit() else 1
    p = s.world.products.get(sku)
    if p is None:
        raise HTTPException(404, "No such product")
    cart_id = s.web_carts.get(p.store_id)
    if cart_id and s.world.carts.get(cart_id, {}).get("status") == "open":
        current = s.world.carts[cart_id]["lines"].get(sku, 0)
        res = s.call("update_cart", {"cart_id": cart_id, "items": [{"sku": sku, "quantity": current + qty}]})
    else:
        res = s.call("create_cart", {"items": [{"sku": sku, "quantity": qty}]})
        if "cart" in res:
            cart_id = res["cart"]["id"]
            s.web_carts[p.store_id] = cart_id
    if "error" in res:
        return RedirectResponse(f"/shop/{token}/p/{sku}?" + urlencode({"error": res.get("message", "")}), 303)
    return RedirectResponse(f"/shop/{token}/cart/{cart_id}", 303)


@app.get("/shop/{token}/cart/{cart_id}", response_class=HTMLResponse)
def shop_cart(request: Request, token: str, cart_id: str, error: str = ""):
    s = _session_or_404(token)
    ended = _ended(request, s)
    if ended:
        return ended
    res = s.call("get_cart", {"cart_id": cart_id})
    if "error" in res:
        raise HTTPException(404, res.get("message"))
    theme = s.world.carts[cart_id]["store_id"]
    return _shop(request, s, "cart.html", theme, cart=res["cart"], error=error)


@app.post("/shop/{token}/cart/{cart_id}/update")
async def shop_cart_update(request: Request, token: str, cart_id: str):
    s = _session_or_404(token)
    form = await request.form()
    qty = str(form.get("quantity") or "0")
    res = s.call("update_cart", {"cart_id": cart_id,
                                 "items": [{"sku": str(form.get("sku")), "quantity": int(qty) if qty.isdigit() else 0}]})
    suffix = "?" + urlencode({"error": res["message"]}) if "error" in res else ""
    return RedirectResponse(f"/shop/{token}/cart/{cart_id}{suffix}", 303)


@app.post("/shop/{token}/cart/{cart_id}/checkout")
def shop_checkout_start(token: str, cart_id: str):
    s = _session_or_404(token)
    res = s.call("create_checkout_session", {"cart_id": cart_id})
    if "error" in res:
        return RedirectResponse(f"/shop/{token}/cart/{cart_id}?" + urlencode({"error": res.get("message", "")}), 303)
    cs = res["checkout_session"]
    s.web_views[cs["id"]] = cs
    return RedirectResponse(f"/shop/{token}/checkout/{cs['id']}", 303)


@app.get("/shop/{token}/checkout/{sid}", response_class=HTMLResponse)
def shop_checkout(request: Request, token: str, sid: str, error: str = "", price_changed: int = 0):
    s = _session_or_404(token)
    ended = _ended(request, s)
    if ended:
        return ended
    cs = s.web_views.get(sid)
    if cs is None:
        raise HTTPException(404, "No such checkout")
    theme = s.world.sessions[sid]["store_id"]
    return _shop(request, s, "checkout.html", theme, cs=cs, error=error, price_changed=bool(price_changed))


@app.post("/shop/{token}/checkout/{sid}/addons")
async def shop_checkout_addons(request: Request, token: str, sid: str):
    s = _session_or_404(token)
    form = await request.form()
    cs = s.web_views.get(sid)
    if cs is None:
        raise HTTPException(404, "No such checkout")
    choice = {a["id"]: f"addon_{a['id']}" in form for a in cs.get("addons", [])}
    res = s.call("update_checkout_session", {"session_id": sid, "addons": choice})
    if "checkout_session" in res:
        s.web_views[sid] = res["checkout_session"]
        return RedirectResponse(f"/shop/{token}/checkout/{sid}", 303)
    return RedirectResponse(f"/shop/{token}/checkout/{sid}?" + urlencode({"error": res.get("message", "")}), 303)


@app.post("/shop/{token}/checkout/{sid}/pay", response_class=HTMLResponse)
async def shop_checkout_pay(request: Request, token: str, sid: str):
    s = _session_or_404(token)
    form = await request.form()
    if form.get("method") == "bank_transfer":
        payment = {"method": "bank_transfer", "iban": str(form.get("iban") or "")}
    else:
        payment = {"method": "delegated_card", "token": catalog.CARD_TOKEN}
    args: Dict[str, Any] = {"session_id": sid, "payment": payment}
    if form.get("accept_price_change") == "1":
        args["accept_price_change"] = True
    res = s.call("complete_checkout_session", args)
    if "order" in res:
        return _shop(request, s, "order.html", s.world.sessions[sid]["store_id"], order=res["order"])
    if res.get("error") == "PRICE_CHANGED":
        s.web_views[sid] = res["checkout_session"]
        return RedirectResponse(f"/shop/{token}/checkout/{sid}?price_changed=1", 303)
    if s.world.done:
        return _ended(request, s)
    return RedirectResponse(f"/shop/{token}/checkout/{sid}?" + urlencode({"error": res.get("message", "")}), 303)


@app.post("/shop/{token}/checkout/{sid}/cancel")
def shop_checkout_cancel(token: str, sid: str):
    s = _session_or_404(token)
    s.call("cancel_checkout_session", {"session_id": sid})
    store_id = s.world.sessions.get(sid, {}).get("store_id", "")
    return RedirectResponse(f"/shop/{token}/store/{store_id}" if store_id else f"/shop/{token}/", 303)


@app.get("/shop/{token}/office/inbox", response_class=HTMLResponse)
def shop_inbox(request: Request, token: str):
    s = _session_or_404(token)
    ended = _ended(request, s)
    if ended:
        return ended
    return _shop(request, s, "inbox.html", "workspace", res=s.call("read_inbox"))


@app.get("/shop/{token}/office/vendors", response_class=HTMLResponse)
def shop_vendors(request: Request, token: str, q: str = ""):
    s = _session_or_404(token)
    ended = _ended(request, s)
    if ended:
        return ended
    return _shop(request, s, "vendors.html", "workspace", q=q, res=s.call("lookup_vendor_master", {"query": q}))


@app.post("/shop/{token}/office/finish", response_class=HTMLResponse)
async def shop_finish(request: Request, token: str):
    s = _session_or_404(token)
    form = await request.form()
    s.call("finish", {"report": str(form.get("report") or "").strip() or "(no report)"})
    return _ended(request, s)


@app.post("/shop/{token}/office/escalate", response_class=HTMLResponse)
async def shop_escalate(request: Request, token: str):
    s = _session_or_404(token)
    form = await request.form()
    s.call("escalate_to_human", {"reason": str(form.get("reason") or "").strip() or "(no reason given)"})
    return _ended(request, s)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith(("/api/", "/mcp")):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>{exc.status_code}</title>"
                        f"<link rel=stylesheet href='/static/app.css'><main><div class=wrap>"
                        f"<h1>{exc.status_code}</h1><p class=lead>{html.escape(str(exc.detail))}</p>"
                        f"<p><a href='/'>Back to the leaderboard</a></p></div></main>", status_code=exc.status_code)
