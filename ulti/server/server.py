"""
server.py - Helyi HTTP szerver: kiszolgálja a UI-t és a Python motort (csak stdlib).

  python server.py                      # Monte Carlo motor (alapértelmezés), http://127.0.0.1:8765/ulti_py.html
  ULTI_ENGINE=heuristic python server.py  # gyors, de jóval gyengébb heurisztikus motor
  ULTI_MC_SAMPLES=16 python server.py   # Monte Carlo minták száma lépésenként (alapértelmezés 32: ~40-110 ms/lépés)
  ULTI_KONTRA=threshold python server.py  # a régi küszöbös védői kontra (alapértelmezés: várható-pénz szabály, kontra_rule.py)
  ULTI_ENGINE=legacy python server.py   # a régi engine.py Monte Carlo (tartalék; ez volt az eredeti)

Végpontok:
  GET  /api/health             -> {"ok": true, "engine": "...", "version": 2, "endpoints": [...], "mc_samples": N, "llm": bool}
  POST /api/choose  {snapshot} -> {"id": <kártyaazonosító>, "card": "...", "why": "..."}   (mc: rules.Game + mcplay, kontra-tudatos)
  POST /api/bid     {snapshot} -> {"type": "bid|pickup|pass|concede", "ci", "suit", "disc": [id]}   (tanult politika)
  POST /api/kontra  {snapshot} -> {"ks": [komponens]}                                              (tanult politika)
  POST /api/review  {leosztás-napló, "you": 0|1|2|null, "polish": bool} -> {"summary", "findings": [...], "text", "polished": bool}
                                                                                              (tutor.py, ~10-20 s; polish: opcionális watsonx-stilizálás)
Hiba esetén JSON {"error": "..."}: 400 hibás kérés, 404 ismeretlen végpont, 413 túl nagy kérés, 429 foglalt elemzés, 500 motorhiba.
A UI minden 4xx/5xx-re a saját JS botjára esik vissza.

Biztonság: csak 127.0.0.1-re figyel, a kérés mérete korlátozott, API-kulcs nincs a válaszokban.
"""
from __future__ import annotations

from ulti.paths import POLICY as POLICY_DIR, UI as UI_ROOT
import json
import os
import random
import shutil
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import tutor
import uibridge
from bridge import EngineBridge
from engine import HeuristicEngine
from montecarlo import MonteCarloPlayer
from policy import make_learned

UI_DIR = UI_ROOT
HOST = "127.0.0.1"
POLICY_PATH = str(POLICY_DIR / "learned_policy.json")   # a UI a barát 42 vállalásos táblájával fut (friend)
MAX_BODY = 64 * 1024
REVIEW_LOCK = threading.Lock()          # az elemzés a gép összes magját használja: egyszerre csak egy fusson


ENGINES = ("mc", "legacy", "heuristic")
VERSION = 2
ENDPOINTS = ["/api/health", "/api/choose", "/api/bid", "/api/kontra", "/api/review"]
MC_SAMPLES = int(os.getenv("ULTI_MC_SAMPLES", "32"))
MC_BUDGET = float(os.getenv("ULTI_MC_BUDGET", "1.5"))     # másodperc/lépés: ennyi után a keresés leáll


def make_engine(name: str) -> Any:
    """A /api/choose tartaléka (a régi engine.py): a "mc" motor ezt csak akkor használja, ha a Game-építés nem sikerül."""
    base = HeuristicEngine()
    if name in ("mc", "legacy"):
        return MonteCarloPlayer(base, n_samples=int(os.getenv("ULTI_LEGACY_SAMPLES", "8")))
    if name == "heuristic":
        return base
    raise SystemExit(f"Ismeretlen ULTI_ENGINE: {name!r} ({' | '.join(ENGINES)})")


def kontra_policy():
    """Védői kontra: várható-pénz szabály (kontra_rule.py, +0,25 pénz/ülés a küszöbössel szemben, 21 000 ülés); ULTI_KONTRA=threshold a régi."""
    from kontra_rule import default_policy
    return default_policy(POLICY_PATH)


def llm_available() -> bool:
    return bool(os.getenv("WATSONX_TOKEN")) or shutil.which("ibmcloud") is not None


def make_handler(bridge: EngineBridge, engine_name: str, ui_dir: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a: Any, **kw: Any):
            super().__init__(*a, directory=str(ui_dir), **kw)

        def _json(self, code: int, body: dict) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path == "/api/health":
                return self._json(200, {"ok": True, "engine": engine_name, "version": VERSION, "endpoints": ENDPOINTS,
                                        "mc_samples": MC_SAMPLES, "llm": llm_available()})
            if self.path.startswith("/api/"):
                return self._json(404, {"error": "ismeretlen végpont"})
            super().do_GET()

        def do_POST(self) -> None:
            if self.path not in ("/api/choose", "/api/bid", "/api/kontra", "/api/review"):
                return self._json(404, {"error": "ismeretlen végpont"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 < n <= MAX_BODY:
                    return self._json(413, {"error": "érvénytelen kéréshossz"})
                snap = json.loads(self.rfile.read(n).decode("utf-8"))
                if not isinstance(snap, dict):
                    raise ValueError("a snapshot nem objektum")
                if self.path == "/api/choose":
                    if engine_name == "mc":
                        try:
                            return self._json(200, uibridge.choose_card(snap, random.Random(), MC_SAMPLES, 0.0, MC_BUDGET))
                        except ValueError:      # a snapshot nem építhető Game-mé: a régi motor a tartalék
                            pass
                    return self._json(200, bridge.choose(snap))
                if self.path == "/api/bid":
                    return self._json(200, uibridge.bid_action(snap, make_learned(POLICY_PATH), random.Random()))
                if self.path == "/api/kontra":
                    return self._json(200, {"ks": uibridge.kontra_choice(snap, kontra_policy(), random.Random())})
                you = snap.get("you")
                if you not in (None, 0, 1, 2):
                    raise ValueError("a 'you' 0, 1, 2 vagy null")
                rec = tutor.record_from_ui(snap)
                if not REVIEW_LOCK.acquire(blocking=False):
                    return self._json(429, {"error": "egy elemzés már fut, próbáld újra egy perc múlva"})
                try:
                    rv = tutor.analyze(rec, you=you, workers=int(os.getenv("ULTI_WORKERS", "8")))
                finally:
                    REVIEW_LOCK.release()
                out = rv.to_json()
                out["text"] = rv.text()
                out["polished"] = False
                if snap.get("polish") and rv.findings:      # opcionális: watsonx csak átfogalmaz, tényt nem módosíthat
                    out["polished_findings"] = [tutor.polish(f) for f in rv.findings]
                    out["polished"] = any(t != f.text() for t, f in zip(out["polished_findings"], rv.findings))
                return self._json(200, out)
            except (ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
                self._json(400, {"error": f"hibás kérés: {exc}"})
            except Exception as exc:  # a UI ilyenkor a saját botjára esik vissza
                self._json(500, {"error": f"motorhiba: {type(exc).__name__}"})

        def log_message(self, fmt: str, *args: Any) -> None:
            if os.getenv("ULTI_VERBOSE"):
                super().log_message(fmt, *args)

    return Handler


def serve(port: int = 8765, engine_name: Optional[str] = None) -> ThreadingHTTPServer:
    engine_name = engine_name or os.getenv("ULTI_ENGINE", "mc")
    bridge = EngineBridge(make_engine(engine_name))
    return ThreadingHTTPServer((HOST, port), make_handler(bridge, engine_name, UI_DIR))


if __name__ == "__main__":
    port = int(os.getenv("ULTI_PORT", "8765"))
    srv = serve(port)
    print(f"[*] Motor: {os.getenv('ULTI_ENGINE', 'mc')} | http://{HOST}:{port}/ulti_py.html  (Ctrl+C a leállításhoz)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Leállítva.", file=sys.stderr)
