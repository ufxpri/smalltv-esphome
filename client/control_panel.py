#!/usr/bin/env python3
"""SmallTV control panel — a local web UI.

Runs a tiny local web server (opened in your browser). Switch what streams to
the device, set brightness, pick stickers, and watch a live monitor: a mirror
of the rendered screen, a heatmap of the patches being updated, plus fps /
bandwidth / heap / RSSI. Device calls are cached in the background so the UI
stays responsive even while the device is busy streaming.

    python control_panel.py [device_host]
"""
import glob
import io
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cfg_mod  # noqa: E402
import stream  # noqa: E402
from smalltv_stream import TELEM_DIR as TELEM  # noqa: E402

_ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]
# The panel is the settings UI, so the saved config is the source of truth for
# which device to talk to; an explicit argv host still wins for one-off runs.
HOST = _ARGS[0] if _ARGS else cfg_mod.load()["device_ip"]
NO_BROWSER = "--no-browser" in sys.argv     # the widget launches us at login
GIFDIR = stream.gif_dir()
MODE = os.path.join(TELEM, "mode.json")
PORT = 8787
# Derived from stream.SOURCES so a new source only has to be registered once.
SCRIPT_TO_KEY = {v: k for k, v in stream.SOURCES.items()}
_thumbs = {}
STATE = {"online": False, "current": None, "heap": None, "rssi": None, "uptime": None, "host": HOST}
LOCK = threading.Lock()

PAGE = r"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>SmallTV 컨트롤</title><style>
*{box-sizing:border-box;font-family:-apple-system,system-ui,sans-serif}
body{margin:0;background:#0f1013;color:#e7e2da;display:flex;justify-content:center}
.wrap{width:min(460px,94vw);padding:22px}
h1{font-size:17px;font-weight:600;margin:0 0 3px}
#status{font-size:13px;color:#7c8b99;margin-bottom:16px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:11px}
.src{border:0;border-radius:14px;background:#22242b;color:#e7e2da;font-size:16px;padding:18px 0;cursor:pointer;transition:.15s;font-weight:500}
.src:hover{filter:brightness(1.15)}.src.on{color:#fff;box-shadow:0 0 0 2px #fff3 inset}
.src[data-k=furnace].on{background:#c0552f}.src[data-k=stickers].on{background:#3a7d5d}
.src[data-k=stocks].on{background:#2f7d4f}.src[data-k=sectors].on{background:#7d5a2f}
.src[data-k=video].on{background:#3a5a9d}.src[data-k=off].on{background:#555a63}
.mon{margin:18px 0 4px;background:#17181d;border-radius:16px;padding:14px;display:flex;gap:14px;align-items:center}
.screen{position:relative;width:160px;height:160px;flex:none;border-radius:12px;overflow:hidden;background:#000}
.screen img,.screen canvas{position:absolute;inset:0;width:160px;height:160px;image-rendering:pixelated}
.mon .info{font-size:12.5px;line-height:1.7;color:#9aa4b0}
.mon .info b{color:#e7e2da;font-weight:600}
.mon .big{font-size:15px;color:#d97757;font-weight:600;margin-bottom:2px}
.row{display:flex;align-items:center;gap:12px;margin:16px 2px 6px}
.row label{font-size:14px;min-width:34px}
input[type=range]{flex:1;accent-color:#d97757}#bv{min-width:42px;text-align:right;color:#7c8b99;font-size:13px}
.sec{font-size:12px;color:#7c8b99;margin:16px 2px 8px}
.thumbs{display:grid;grid-template-columns:repeat(6,1fr);gap:6px}
.thumbs img{width:100%;aspect-ratio:1;border-radius:9px;background:#22242b;cursor:pointer;border:2px solid transparent;transition:.12s}
.thumbs img:hover{border-color:#d97757}
.seg{display:flex;gap:8px;align-items:center}
.seg button{border:0;border-radius:10px;background:#22242b;color:#e7e2da;padding:9px 14px;cursor:pointer;font-size:13px}
.seg button.on{background:#3a5a9d;color:#fff}
.seg label{font-size:13px;color:#9aa4b0;margin-left:auto;display:flex;gap:6px;align-items:center;cursor:pointer}
.seg input[type=text]{flex:1;border:0;border-radius:10px;background:#22242b;color:#e7e2da;padding:9px 12px;font-size:13px;font-family:ui-monospace,monospace}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.chip{background:#22242b;border-radius:8px;padding:6px 8px 6px 11px;font-size:13px;font-family:ui-monospace,monospace;display:flex;gap:7px;align-items:center}
.chip b{color:#7c8b99;cursor:pointer;font-weight:400}.chip b:hover{color:#ff6b6b}
</style></head><body><div class=wrap>
<h1>SmallTV 컨트롤</h1><div id=status><span class=dot>●</span> …</div>
<div class=grid>
 <button class=src data-k=furnace onclick="sw('furnace')">🔥 용광로</button>
 <button class=src data-k=stickers onclick="sw('stickers')">😺 스티커</button>
 <button class=src data-k=stocks onclick="sw('stocks')">📈 주식</button>
 <button class=src data-k=sectors onclick="sw('sectors')">🗺️ 섹터</button>
 <button class=src data-k=video onclick="vid()">🎥 영상</button>
 <button class=src data-k=off onclick="sw('off')">⏻ 끄기</button>
</div>
<div class=mon>
 <div class=screen><img id=mirror><canvas id=heat width=240 height=240></canvas></div>
 <div class=info><div class=big id=mstats>—</div>
  <div>heap <b id=heap>—</b></div><div>RSSI <b id=rssi>—</b></div>
  <div>uptime <b id=uptime>—</b></div>
  <label style="display:block;margin-top:6px"><input type=checkbox id=heaton checked> 패치 히트맵</label>
 </div></div>
<div class=row><label>밝기</label>
 <input id=br type=range min=10 max=100 value=70 onchange="bri(this.value)" oninput="bv.textContent=this.value+'%'">
 <span id=bv>70%</span></div>
<div class=sec>색심도 / 디더 (수동)</div>
<div class=seg>
 <button id=b16 onclick="cm(16)">16bit · 565</button>
 <button id=b8 onclick="cm(8)">8bit · 332</button>
 <label><input type=checkbox id=dith onchange="cm()"> 디더링</label>
</div>
<div class=sec>주식 티커 — 2개 이상이면 순환합니다</div>
<div class=chips id=chips></div>
<div class=seg>
 <input id=tk type=text spellcheck=false placeholder="AAPL / 005930.KS / BTC-USD">
 <button onclick="addtk()">추가</button>
</div>
<div class=sec>스티커 — 클릭하면 그 스티커만 재생</div>
<div class=thumbs id=th></div>
<div class=sec>기기 주소 — IP 또는 호스트명</div>
<div class=seg>
 <input id=dev type=text spellcheck=false placeholder="192.168.0.10">
 <button onclick="setdev()">저장</button>
</div>
</div><script>
const bv=document.getElementById('bv');
async function post(u){await fetch(u,{method:'POST'})}
function sw(k){post('/switch?src='+k);mark(k)}
function bri(v){post('/brightness?v='+v)}
function vid(){let p=prompt('재생할 영상/움짤 파일 경로');if(p){post('/video?path='+encodeURIComponent(p));mark('video')}}
function pick(n){post('/pick?name='+n);mark('stickers')}
function setdev(){let v=document.getElementById('dev').value.trim();if(v)post('/device?ip='+encodeURIComponent(v))}
// TK is client-owned after the first load: every edit posts immediately, so
// re-syncing it on each poll would only race the user mid-edit.
let TK=[];
function rendertk(){document.getElementById('chips').innerHTML=
 TK.map((t,i)=>'<span class=chip>'+t+'<b onclick="deltk('+i+')">×</b></span>').join('')}
function savetk(){rendertk();post('/tickers?v='+encodeURIComponent(TK.join(',')))}
function addtk(){let e=document.getElementById('tk'),v=e.value.trim().toUpperCase();
 if(v&&!TK.includes(v)){TK.push(v);e.value='';savetk()}}
function deltk(i){TK.splice(i,1);savetk()}
function mark(k){document.querySelectorAll('.src').forEach(b=>b.classList.toggle('on',b.dataset.k===k))}
let CB=16,DI=false;
function cm(bits){if(bits!==undefined)CB=bits;DI=document.getElementById('dith').checked;post('/colormode?bits='+CB+'&dither='+(DI?1:0));markcm()}
function markcm(){document.getElementById('b16').classList.toggle('on',CB==16);document.getElementById('b8').classList.toggle('on',CB==8)}
function fmt(s){s=+s;if(!s)return '—';let h=s/3600|0,m=(s%3600)/60|0;return h?h+'h '+m+'m':m+'m '+(s%60|0)+'s'}
async function tick(){try{let s=await(await fetch('/status')).json();
 document.getElementById('status').innerHTML='<span style="color:'+(s.online?'#5fbf7f':'#bf6b6b')+'">●</span> '+(s.online?'online':'offline')+'  '+s.host;
 mark(s.current);
 heap.textContent=s.heap?((s.heap/1024).toFixed(1)+' KB'):'—';
 rssi.textContent=s.rssi!=null?(Math.round(s.rssi)+' dBm'):'—';
 uptime.textContent=fmt(s.uptime);
 let dv=document.getElementById('dev');
 if(document.activeElement!==dv)dv.value=s.host;   // don't fight the user mid-edit
 CB=s.bits||16;DI=!!s.dither;document.getElementById('dith').checked=DI;markcm()}catch(e){}}
function drawHeat(t){let c=document.getElementById('heat'),x=c.getContext('2d');x.clearRect(0,0,240,240);
 if(!t.grid||!document.getElementById('heaton').checked)return;
 let cw=240/t.gw,ch=240/t.gh;x.fillStyle='rgba(217,119,87,.5)';
 for(let i=0;i<t.grid.length;i++)if(t.grid[i]){x.fillRect((i%t.gw)*cw,((i/t.gw)|0)*ch,cw,ch)}}
async function mon(){document.getElementById('mirror').src='/frame.jpg?'+Date.now();
 try{let t=await(await fetch('/telemetry')).json();
  let fresh=t.ts&&(Date.now()/1000-t.ts<2);
  drawHeat(fresh?t:{});
  mstats.textContent=fresh?(t.fps+' fps · '+t.blits+' patches · '+t.kbps+' KB/s'):'정지됨';
 }catch(e){}}
async function thumbs(){let n=await(await fetch('/stickers')).json();
 th.innerHTML=n.map(x=>'<img src="/thumb?name='+x+'" onclick="pick(\''+x+'\')">').join('')}
document.getElementById('tk').addEventListener('keydown',e=>{if(e.key=='Enter')addtk()});
fetch('/status').then(r=>r.json()).then(s=>{TK=s.tickers||[];rendertk()});
thumbs();tick();setInterval(tick,3000);setInterval(mon,250);
</script></body></html>"""


def dev_get(path):
    try:
        with urllib.request.urlopen(f"http://{HOST}{path}", timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


def set_tickers(csv):
    """Persist the stocks rotation list. Returns the saved list."""
    syms = [t.strip().upper() for t in (csv or "").split(",") if t.strip()]
    c = cfg_mod.load()
    c["tickers"] = syms or ["AAPL"]
    cfg_mod.save(c)
    return c["tickers"]


def set_host(ip):
    """Point the panel at a different device and persist it for the widget."""
    global HOST
    ip = (ip or "").strip()
    if not ip or ip == HOST:
        return
    HOST = ip
    c = cfg_mod.load()
    c["device_ip"] = ip
    cfg_mod.save(c)
    with LOCK:      # drop readings from the old device rather than show them as this one's
        STATE.update(host=ip, online=False, uptime=None, heap=None, rssi=None)


def telem_fresh():
    d = read_file(os.path.join(TELEM, "stat.json"))
    try:
        return d is not None and time.time() - json.loads(d).get("ts", 0) < 3
    except Exception:
        return False


def poller():
    while True:
        up = dev_get("/sensor/uptime")
        heap = dev_get("/sensor/free_heap")
        rssi = dev_get("/sensor/wifi_signal")
        r = stream.running()
        # a live stream proves the device is up even when it's too busy for REST
        online = up is not None or telem_fresh()
        with LOCK:
            STATE.update(online=online, current=SCRIPT_TO_KEY.get(r[0]) if r else None,
                         uptime=up and up.get("value"), heap=heap and heap.get("value"),
                         rssi=rssi and rssi.get("value"))
        time.sleep(3)


def thumb_png(name):
    if name not in _thumbs:
        im = Image.open(os.path.join(GIFDIR, name))
        im.seek(0)
        buf = io.BytesIO()
        im.convert("RGB").resize((72, 72)).save(buf, "PNG")
        _thumbs[name] = buf.getvalue()
    return _thumbs[name]


def read_file(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def q(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.encode())
        elif p == "/status":
            with LOCK:
                st = dict(STATE)
            try:
                st.update(json.loads(read_file(MODE) or b"{}"))
            except Exception:
                pass
            st.setdefault("bits", 16)
            st.setdefault("dither", False)
            st["tickers"] = cfg_mod.load()["tickers"]
            self._send(200, "application/json", json.dumps(st).encode())
        elif p == "/telemetry":
            self._send(200, "application/json", read_file(os.path.join(TELEM, "stat.json")) or b"{}")
        elif p == "/frame.jpg":
            img = read_file(os.path.join(TELEM, "frame.jpg"))
            self._send(200, "image/jpeg", img) if img else self._send(404, "text/plain", b"")
        elif p == "/stickers":
            names = [os.path.basename(x) for x in sorted(glob.glob(os.path.join(GIFDIR, "*.gif")))]
            self._send(200, "application/json", json.dumps(names).encode())
        elif p == "/thumb":
            try:
                self._send(200, "image/png", thumb_png(self.q().get("name", [""])[0]))
            except Exception:
                self._send(404, "text/plain", b"")
        else:
            self._send(404, "text/plain", b"")

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        qs = self.q()
        if p == "/switch":
            src = qs.get("src", [""])[0]
            stream.stop_all()
            if src == "stocks":
                c = cfg_mod.load()
                stream.start(src, [*c["tickers"], "--rotate", str(c["ticker_rotate"])],
                             host=HOST)
            elif src in stream.SOURCES and src != "video":   # video needs a path
                stream.start(src, [], host=HOST)
        elif p == "/device":
            set_host(qs.get("ip", [""])[0])
        elif p == "/tickers":
            set_tickers(qs.get("v", [""])[0])
        elif p == "/video":
            path = qs.get("path", [""])[0]
            if path:
                stream.stop_all()
                stream.start("video", [path], host=HOST)
        elif p == "/pick":
            name = qs.get("name", [""])[0]
            stream.stop_all()
            # via stream.start so the detach flags stay in one place (start_new_session
            # is POSIX-only; Windows needs creationflags instead)
            stream.start("stickers", [GIFDIR, "--pick", name], host=HOST)
        elif p == "/brightness":
            v = int(qs.get("v", ["70"])[0])
            threading.Thread(target=dev_post,
                             args=(f"/light/backlight/turn_on?brightness={int(v * 255 / 100)}",),
                             daemon=True).start()
        elif p == "/colormode":
            bits = 8 if qs.get("bits", ["16"])[0] == "8" else 16
            dither = qs.get("dither", ["0"])[0] in ("1", "true")
            os.makedirs(TELEM, exist_ok=True)
            with open(MODE, "w") as f:
                json.dump({"bits": bits, "dither": dither}, f)
        self._send(200, "text/plain", b"ok")


def dev_post(path):
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://{HOST}{path}", method="POST"), timeout=4)
    except Exception:
        pass


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # cp949 consoles can't encode our logs
    except Exception:
        pass
    threading.Thread(target=poller, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"control panel: {url}  (device {HOST})", flush=True)
    if not NO_BROWSER:      # the widget starts us at login; don't pop a tab every boot
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
