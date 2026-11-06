#!/usr/bin/env python3
"""DripVault UI server — serves the single-file UI and history.json.

Serves:
  GET /                → web/index.html
  GET /history.json    → history.json (the leg record the scheduler appends to)
  POST /api/plan       → schedule preview (same logic as scripts/plan.py)
  POST /api/quote      → live dry-run quote (dry=true, nothing executes)

No framework, stdlib only. Binds 127.0.0.1 by default.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DIR / "scripts"))

HISTORY = DIR / "history.json"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # history.json contains no secrets (addresses + amounts only), but the
        # UI is a local dashboard: do not cache or expose beyond localhost
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802 (stdlib API)
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            f = DIR / "web" / "index.html"
            if f.exists():
                self._send(200, f.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, b"UI not found", "text/plain")
        elif path == "/history.json":
            data = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
            self._json(200, data)
        elif path == "/api/chains":
            try:
                import quote as Q
                chains = sorted(set(
                    t.get("blockchain", "") for t in Q.fetch(Q.TOKENS_URL)))
                self._json(200, {"chains": chains})
            except Exception:
                self._json(200, {"chains": ["eth", "base", "arb", "polygon",
                                            "bsc", "avax", "sol", "near", "btc"]})
        elif path == "/health":
            self._json(200, {"ok": True, "service": "dripvault-ui"})
        else:
            self._json(404, {"error": "not found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path not in ("/api/plan", "/api/quote"):
            self._json(404, {"error": "not found", "path": path})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json(400, {"error": "invalid JSON body"})
            return
        if path == "/api/plan":
            try:
                from plan import build_schedule  # scripts/plan.py
                self._json(200, build_schedule(
                    float(body["total"]), int(body["drips"]),
                    body.get("interval", "24h"), float(body.get("slippage", 1.0))))
            except Exception as e:
                self._json(400, {"error": str(e)[:200]})
            return
        # /api/quote — live dry-run quote, never executes
        try:
            import quote as Q
            src = Q.resolve_asset_id(body["from"], body["token"])
            dst = Q.resolve_asset_id(body["to"], body["asset"])
            if not src or not dst:
                self._json(400, {"error": "asset resolution failed"})
                return
            units = int(float(body["amount"]) * 10 ** Q.token_decimals(src))
            q = Q.get_quote(src, dst, str(units),
                            slippage_bps=int(float(body.get("slippage", 1.0)) * 100),
                            dry=True)
            if "http_error" in q:
                self._json(502, {"error": q["http_error"], "detail": q.get("detail", "")[:200]})
                return
            self._json(200, {"mode": "DRY-RUN (nothing executed)", "quote": q.get("quote", q)})
        except Exception as e:
            self._json(400, {"error": str(e)[:200]})


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"DripVault UI → http://127.0.0.1:{port}  (Ctrl-C to stop)")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
