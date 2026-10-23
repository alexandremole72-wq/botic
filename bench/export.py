"""Static copy of the dashboard: leaderboard, runs, missions and every replay as
plain HTML files, to publish results anywhere (GitHub Pages, Vercel, a zip)
without running a server.

    python -m bench export-site --out site/
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Optional

from .storage import RunStore


def export_site(out_dir: str, db: Optional[str] = None, models_only: bool = False) -> int:
    from fastapi.testclient import TestClient

    from . import web

    store = RunStore(db)
    web._store = store
    records = store.list(limit=100_000, include_scripted=not models_only)
    pages = {"index.html": "/", "runs.html": "/runs", "missions.html": "/missions"}
    pages.update({f"run-{r['id']}.html": f"/runs/{r['id']}" for r in records})
    os.makedirs(out_dir, exist_ok=True)

    def rewrite(html: str) -> str:
        html = re.sub(r'https?://[^"\']+/static/', "static/", html)
        html = re.sub(r'href="/runs/([0-9a-f]{12})"', r'href="run-\1.html"', html)
        html = re.sub(r'href="/runs(\?[^"]*)?"', 'href="runs.html"', html)
        html = re.sub(r'href="/missions(#[^"]*)?"', lambda m: f'href="missions.html{m.group(1) or ""}"', html)
        html = html.replace('href="/"', 'href="index.html"')
        return html

    with TestClient(web.app) as client:
        for name, path in pages.items():
            resp = client.get(path, params={"export": "1"})
            resp.raise_for_status()
            with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
                f.write(rewrite(resp.text))
    static_out = os.path.join(out_dir, "static")
    shutil.rmtree(static_out, ignore_errors=True)
    shutil.copytree(os.path.join(web.HERE, "static"), static_out, ignore=shutil.ignore_patterns("shop.css"))
    return len(pages)
