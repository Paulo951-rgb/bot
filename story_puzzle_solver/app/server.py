"""Local web dashboard + REST API (rules 61, 62, 32-38).

A zero-dependency local server (stdlib http.server) that serves the FAST ENTRY
UI + surveillance dashboard and exposes the pipeline state via JSON endpoints.
Run with ``python -m story_puzzle_solver.app.server``. The UI is a single HTML
page with inline CSS/JS.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from ..common.logger import JsonLogger
from ..pipeline import PuzzlePipeline
from ..source.base import StorySource


class DashboardServer:
    def __init__(self, pipeline: PuzzlePipeline, source: Optional[StorySource] = None,
                 host: str = "127.0.0.1", port: int = 8765,
                 poll_interval_ms: int = 250, logger: Optional[JsonLogger] = None):
        self.pipeline = pipeline
        self.source = source
        self.host = host
        self.port = port
        self.poll_interval_s = poll_interval_ms / 1000.0
        self._logger = logger or JsonLogger("dashboard")
        self._stop = threading.Event()
        self._server: Optional[ThreadingHTTPServer] = None
        self._poller: Optional[threading.Thread] = None
        self._last_result = None

    def start(self, block: bool = True) -> None:
        if self.source is not None:
            self._poller = threading.Thread(target=self._poll_loop, daemon=True)
            self._poller.start()
        srv = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        self._server = srv
        self._logger.info("dashboard_start", host=self.host, port=self.port)
        if block:
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                self.stop()
        else:
            threading.Thread(target=srv.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._server:
            self._server.shutdown()

    def _poll_loop(self) -> None:
        assert self.source is not None
        while not self._stop.is_set():
            try:
                items = self.source.poll()
                for it in items:
                    import tempfile
                    from ..common.timing import Timer
                    dest = Path(tempfile.mkdtemp()) / (it.story_id + Path(it.media_path).suffix)
                    dl_ms = 0.0
                    try:
                        with Timer() as tdl:
                            p = self.source.get_media(it, dest)
                        dl_ms = tdl.elapsed_ms
                        self.pipeline.metrics.record("download_latency_ms", dl_ms)
                    except Exception as e:
                        # source temporarily unavailable (rule 19): mark + continue
                        self.pipeline.set_source_status("SOURCE_UNAVAILABLE")
                        self._logger.warn("source_get_media_error", error=str(e))
                        continue
                    r = self.pipeline.process(it, p)
                    r.download_latency_ms = dl_ms
                    self._last_result = r
            except Exception as e:
                self.pipeline.set_source_status("SOURCE_UNAVAILABLE")
                self._logger.warn("dashboard_poll_error", error=str(e))
            self._stop.wait(self.poll_interval_s)

    def _state_json(self) -> dict:
        fe = self.pipeline.fast_entry()
        snap = self.pipeline.state.snapshot()
        m = self.pipeline.metrics.snapshot()
        lr = self._last_result
        return {
            "fast_entry": fe,
            "regions": snap,
            "metrics": m,
            "source_status": self.pipeline.source_status,
            "vision_status": self.pipeline.vision_status(),
            "last_story": {
                "story_id": lr.story_id, "media_kind": lr.media_kind,
                "card_detected": lr.card_detected, "notifications": lr.notifications,
                "media_to_result_ms": lr.media_to_result_ms,
                "download_latency_ms": lr.download_latency_ms,
            } if lr else None,
            "notifications": [r.__dict__ for r in self.pipeline.notifications.history()[-20:]],
        }

    def _make_handler(self) -> type:
        pipeline = self.pipeline
        state_fn = self._state_json
        ui_html = _UI_HTML

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                if self.path == "/api/state":
                    data = json.dumps(state_fn(), ensure_ascii=False).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                elif self.path in ("/", "/index.html"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(ui_html.encode())
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if self.path == "/api/copy":
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length) or b"{}")
                    field = body.get("field", "")
                    fe = pipeline.fast_entry()
                    val = ""
                    if field == "all":
                        parts = []
                        for f in ("number", "name", "exp", "cvv"):
                            v = fe.get(f, {}).get("clipboard", "")
                            if v:
                                parts.append(v)
                        val = "\n".join(parts)
                    elif field in fe:
                        val = fe[field].get("clipboard", "")
                    ok = pipeline.clipboard.copy(val) if val else False
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": ok, "field": field}).encode())
                else:
                    self.send_response(404)
                    self.end_headers()

        return Handler


_UI_HTML = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>Story Puzzle Solver</title>
<style>
:root{--bg:#0f1117;--card:#1a1d27;--accent:#4f8cff;--ok:#3ddc84;--warn:#ffb454;--err:#ff5c5c;--txt:#e6e8ee;--dim:#8b90a0}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--txt);display:flex;gap:18px;padding:18px;min-height:100vh}
.col{flex:1;display:flex;flex-direction:column;gap:14px}
.panel{background:var(--card);border-radius:14px;padding:18px;box-shadow:0 2px 10px rgba(0,0,0,.3)}
h2{font-size:15px;font-weight:600;color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin-bottom:12px}
.status-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.chip{background:#262a36;border-radius:8px;padding:6px 12px;font-size:13px}
.chip b{color:var(--accent)}
.metric{font-size:13px;color:var(--dim);margin:4px 0}
.field{margin:14px 0}
.field label{display:block;font-size:12px;color:var(--dim);text-transform:uppercase;margin-bottom:6px;letter-spacing:1px}
.field .val{font-size:22px;font-weight:600;letter-spacing:2px;font-family:ui-monospace,Consolas,monospace}
.field .partial{color:var(--warn)}
.btn{background:var(--accent);color:#fff;border:none;border-radius:8px;padding:7px 16px;font-size:13px;cursor:pointer;font-weight:600;margin-left:10px;transition:.15s}
.btn:hover{filter:brightness(1.1)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn.all{background:var(--ok);display:block;margin:18px auto 0;font-size:15px;padding:10px 30px}
.copied{color:var(--ok);font-size:12px;margin-left:8px;opacity:0;transition:.2s}
.copied.show{opacity:1}
.notif{padding:6px 10px;border-radius:6px;margin:5px 0;font-size:12px;background:#262a36}
.notif.new{border-left:3px solid var(--ok)}
.notif.correction{border-left:3px solid var(--warn)}
@media(max-width:900px){body{flex-direction:column}}
</style></head><body>
<div class="col">
<div class="panel"><h2>Surveillance</h2><div class="status-row" id="status"></div><div class="metric" id="metrics"></div></div>
<div class="panel"><h2>Notifications</h2><div id="notifs"></div></div>
</div>
<div class="col">
<div class="panel"><h2>Saisie rapide</h2>
<div class="field"><label>Numero</label><div><span class="val" id="v-number"></span><button class="btn" onclick="copy('number')">COPIER</button><span class="copied" id="c-number">Copie</span></div></div>
<div class="field"><label>Nom</label><div><span class="val" id="v-name"></span><button class="btn" onclick="copy('name')">COPIER</button><span class="copied" id="c-name">Copie</span></div></div>
<div class="field"><label>Expiration</label><div><span class="val" id="v-exp"></span><button class="btn" onclick="copy('exp')">COPIER</button><span class="copied" id="c-exp">Copie</span></div></div>
<div class="field"><label>Code</label><div><span class="val" id="v-cvv"></span><button class="btn" onclick="copy('cvv')">COPIER</button><span class="copied" id="c-cvv">Copie</span></div></div>
<button class="btn all" onclick="copy('all')">COPIER TOUT</button>
</div></div>
<script>
async function poll(){try{const r=await fetch('/api/state');const d=await r.json();render(d)}catch(e){}}
function render(d){
const fe=d.fast_entry||{};
for(const f of['number','name','exp','cvv']){
 const el=document.getElementById('v-'+f);if(!el)continue;const v=fe[f]||{};
 el.textContent=v.display||'????';el.className='val'+(v.partial?' partial':'');
 const btn=el.parentElement.querySelector('.btn');if(btn)btn.disabled=!v.clipboard;
}
const ls=d.last_story||{};
const ss=d.source_status||'DISCONNECTED';const vs=d.vision_status||'UNAVAILABLE';
document.getElementById('status').innerHTML=`<span class="chip">Source: <b>${ss}</b></span><span class="chip">Vision: <b>${vs}</b></span><span class="chip">Story: <b>${ls.story_id||'-'}</b></span><span class="chip">Type: <b>${ls.media_kind||'-'}</b></span><span class="chip">Carte: <b>${ls.card_detected?'oui':'non'}</b></span><span class="chip">Latence: <b>${Math.round(ls.media_to_result_ms||0)}ms</b></span>`;
const m=d.metrics||{};const mt=m.media_to_result_ms||{};const t=m.total_latency_ms||{};
document.getElementById('metrics').innerHTML=`Media-Resultat p50: <b>${Math.round(mt.p50||0)}ms</b> p90: <b>${Math.round(mt.p90||0)}ms</b> Total p50: <b>${Math.round(t.p50||0)}ms</b>`;
const ns=d.notifications||[];
document.getElementById('notifs').innerHTML=ns.slice(-8).reverse().map(n=>`<div class="notif ${n.kind}">${n.title} - ${n.body}</div>`).join('')||'<div class="notif">Aucune notification</div>';
}
async function copy(f){try{const r=await fetch('/api/copy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({field:f})});const d=await r.json();if(d.ok){const c=document.getElementById('c-'+f);if(c){c.classList.add('show');setTimeout(()=>c.classList.remove('show'),1500)}}}catch(e){alert('Copie echouee')}}
window.addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='1'){e.preventDefault();copy('number')}if(e.ctrlKey&&e.key==='2'){e.preventDefault();copy('name')}if(e.ctrlKey&&e.key==='3'){e.preventDefault();copy('exp')}if(e.ctrlKey&&e.key==='4'){e.preventDefault();copy('cvv')}if(e.ctrlKey&&e.key==='5'){e.preventDefault();copy('all')}});
setInterval(poll,500);poll();
</script></body></html>"""
