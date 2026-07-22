#!/usr/bin/env python3
"""SmallTV control panel — a local web UI.

Runs a tiny local web server (opened in your browser). Switch what streams to
the device, set brightness, pick stickers, and watch a live monitor: a mirror
of the rendered screen, a heatmap of the patches being updated, plus fps /
bandwidth / heap / RSSI. Device calls are cached in the background so the UI
stays responsive even while the device is busy streaming.

Layout: global settings (device / brightness / colour depth / monitor) stay at
the top, then a source picker whose settings pane swaps to match the selected
source. Nothing is applied while you type or drag — picking a source only
*selects* it; edits land on the device when you press 저장 / 전송. Those posts
are fire-and-forget on the browser side and run in a worker thread here, so a
click never waits on a 3 s process teardown or a busy device.

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
.card{background:#17181d;border-radius:16px;padding:14px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:11px}
.src{border:0;border-radius:14px;background:#22242b;color:#e7e2da;font-size:16px;padding:18px 0;cursor:pointer;transition:.15s;font-weight:500;position:relative}
.src:hover{filter:brightness(1.15)}
.src.sel{box-shadow:0 0 0 2px #d97757 inset}
.src.on{color:#fff}
.src[data-k=furnace].on{background:#c0552f}.src[data-k=stickers].on{background:#3a7d5d}
.src[data-k=stocks].on{background:#2f7d4f}.src[data-k=sectors].on{background:#7d5a2f}
.src[data-k=video].on{background:#3a5a9d}.src[data-k=off].on{background:#555a63}
.mon{display:flex;gap:14px;align-items:center}
.screen{position:relative;width:160px;height:160px;flex:none;border-radius:12px;overflow:hidden;background:#000}
.screen img,.screen canvas{position:absolute;inset:0;width:160px;height:160px;image-rendering:pixelated}
.mon .info{font-size:12.5px;line-height:1.7;color:#9aa4b0}
.mon .info b{color:#e7e2da;font-weight:600}
.mon .big{font-size:15px;color:#d97757;font-weight:600;margin-bottom:2px}
.row{display:flex;align-items:center;gap:12px;margin:10px 2px}
.row label{font-size:14px;min-width:34px}
input[type=range]{flex:1;accent-color:#d97757}#bv{min-width:42px;text-align:right;color:#7c8b99;font-size:13px}
.sec{font-size:12px;color:#7c8b99;margin:14px 2px 8px}
.sec:first-child{margin-top:2px}
.thumbs{display:grid;grid-template-columns:repeat(6,1fr);gap:6px}
.thumbs img{width:100%;aspect-ratio:1;border-radius:9px;background:#22242b;cursor:pointer;border:2px solid transparent;transition:.12s}
.thumbs img:hover{border-color:#d97757}.thumbs img.sel{border-color:#d97757}
.seg{display:flex;gap:8px;align-items:center}
.seg button{border:0;border-radius:10px;background:#22242b;color:#e7e2da;padding:9px 14px;cursor:pointer;font-size:13px}
.seg button.on{background:#3a5a9d;color:#fff}
.seg label{font-size:13px;color:#9aa4b0;margin-left:auto;display:flex;gap:6px;align-items:center;cursor:pointer}
.seg input{flex:1;min-width:0;border:0;border-radius:10px;background:#22242b;color:#e7e2da;padding:9px 12px;font-size:13px;font-family:ui-monospace,monospace}
.seg input[type=number]{flex:none;width:80px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.chip{background:#22242b;border-radius:8px;padding:6px 8px 6px 11px;font-size:13px;font-family:ui-monospace,monospace;display:flex;gap:7px;align-items:center}
.chip b{color:#7c8b99;cursor:pointer;font-weight:400}.chip b:hover{color:#ff6b6b}
.presets{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.presets button{border:0;border-radius:8px;background:#1b2530;color:#9fc4e0;padding:6px 11px;cursor:pointer;font-size:13px}
.presets button:hover{background:#243444}.presets button:disabled{opacity:.4;cursor:default}
.apply{width:100%;border:0;border-radius:12px;background:#d97757;color:#fff;font-size:15px;font-weight:600;padding:13px 0;margin-top:14px;cursor:pointer}
.apply:hover{filter:brightness(1.1)}
.apply.dirty::after{content:' •'}
.hint{font-size:12px;color:#7c8b99;margin-top:8px;text-align:center;min-height:15px}
.pane{display:none}.pane.show{display:block}
</style></head><body><div class=wrap>
<h1>SmallTV 컨트롤</h1><div id=status><span class=dot>●</span> …</div>

<div class=card>
 <div class=mon>
  <div class=screen><img id=mirror><canvas id=heat width=240 height=240></canvas></div>
  <div class=info><div class=big id=mstats>—</div>
   <div>heap <b id=heap>—</b></div><div>RSSI <b id=rssi>—</b></div>
   <div>uptime <b id=uptime>—</b></div>
   <label style="display:block;margin-top:6px"><input type=checkbox id=heaton checked> 패치 히트맵</label>
  </div></div>
</div>

<div class=card>
 <div class=sec>전역 설정</div>
 <div class=row><label>밝기</label>
  <input id=br type=range min=10 max=100 value=70 oninput="bv.textContent=this.value+'%';dirty('g')">
  <span id=bv>70%</span></div>
 <div class=sec>색심도 / 디더 (수동)</div>
 <div class=seg>
  <button id=b16 onclick="cm(16)">16bit · 565</button>
  <button id=b8 onclick="cm(8)">8bit · 332</button>
  <label><input type=checkbox id=dith onchange="dirty('g')"> 디더링</label>
 </div>
 <div class=sec>기기 주소 — IP 또는 호스트명</div>
 <div class=seg><input id=dev type=text spellcheck=false placeholder="192.168.0.10" oninput="dirty('g')"></div>
 <button class=apply id=gsave onclick="saveGlobal()">저장</button>
 <div class=hint id=ghint></div>
</div>

<div class=card>
 <div class=sec>소스 — 고른 뒤 아래 전송을 누르면 적용됩니다</div>
 <div class=grid>
  <button class=src data-k=furnace onclick="sel('furnace')">🔥 용광로</button>
  <button class=src data-k=stickers onclick="sel('stickers')">😺 스티커</button>
  <button class=src data-k=stocks onclick="sel('stocks')">📈 주식</button>
  <button class=src data-k=sectors onclick="sel('sectors')">🗺️ 섹터</button>
  <button class=src data-k=video onclick="sel('video')">🎥 영상</button>
  <button class=src data-k=off onclick="sel('off')">⏻ 끄기</button>
 </div>

 <div class=pane data-p=furnace><div class=sec>CPU 부하를 용광로 불꽃으로 그립니다. 설정 없음.</div></div>
 <div class=pane data-p=sectors><div class=sec>S&amp;P 섹터 히트맵. 설정 없음.</div></div>
 <div class=pane data-p=off><div class=sec>스트리밍을 멈추고 기기의 로컬 시계 화면으로 돌아갑니다.</div></div>

 <div class=pane data-p=stocks>
  <div class=sec>티커 — 2개 이상이면 순환합니다</div>
  <div class=chips id=chips></div>
  <div class=presets id=presets></div>
  <div class=seg>
   <input id=tk type=text spellcheck=false placeholder="AAPL / 005930.KS / BTC-USD">
   <button onclick="addtk()">추가</button>
  </div>
  <div class=sec>순환 간격 (초)</div>
  <div class=seg><input id=rot type=number min=3 max=600 step=1 value=15 oninput="dirty('s')"></div>
 </div>

 <div class=pane data-p=stickers>
  <div class=sec>재생할 스티커 — 고르지 않으면 전체를 순환합니다</div>
  <div class=thumbs id=th></div>
 </div>

 <div class=pane data-p=video>
  <div class=sec>재생할 영상 / 움짤 파일 경로</div>
  <div class=seg><input id=vpath type=text spellcheck=false placeholder="C:\path\to\clip.mp4" oninput="dirty('s')"></div>
 </div>

 <button class=apply id=send onclick="sendSrc()">전송</button>
 <div class=hint id=shint></div>
</div>
</div><script>
const $=id=>document.getElementById(id),bv=$('bv');
// Fire-and-forget: a switch tears down the old source (up to 3 s) and the panel
// answers before that finishes, but there is still nothing here worth waiting on.
function post(u){fetch(u,{method:'POST'}).catch(e=>{})}
function flash(el,msg){el.textContent=msg;clearTimeout(el._t);el._t=setTimeout(()=>el.textContent='',2500)}
function dirty(w){(w=='g'?$('gsave'):$('send')).classList.add('dirty')}
function clean(w){(w=='g'?$('gsave'):$('send')).classList.remove('dirty')}

// ---- global settings (applied only by 저장) ----
let CB=16;
function cm(bits){CB=bits;markcm();dirty('g')}
function markcm(){$('b16').classList.toggle('on',CB==16);$('b8').classList.toggle('on',CB==8)}
function saveGlobal(){
 let q='/settings?bits='+CB+'&dither='+($('dith').checked?1:0)+'&brightness='+$('br').value;
 let ip=$('dev').value.trim();if(ip)q+='&ip='+encodeURIComponent(ip);
 post(q);clean('g');flash($('ghint'),'저장했습니다')}

// ---- source selection (applied only by 전송) ----
let SEL='furnace',CUR=null,TK=[],PICK='';
function sel(k){SEL=k;marksel();
 document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('show',p.dataset.p===k));
 $('send').textContent=k=='off'?'중지':'전송';dirty('s')}
function marksel(){document.querySelectorAll('.src').forEach(b=>{
 b.classList.toggle('sel',b.dataset.k===SEL);b.classList.toggle('on',b.dataset.k===CUR)})}
function sendSrc(){
 let q='/apply?src='+SEL;
 if(SEL=='stocks')q+='&tickers='+encodeURIComponent(TK.join(','))+'&rotate='+($('rot').value||15);
 if(SEL=='stickers')q+='&pick='+encodeURIComponent(PICK);
 if(SEL=='video'){let p=$('vpath').value.trim();
  if(!p){flash($('shint'),'파일 경로를 입력하세요');return}
  q+='&path='+encodeURIComponent(p)}
 post(q);CUR=SEL;marksel();clean('s');flash($('shint'),SEL=='off'?'중지 요청됨':'전송했습니다')}

// One-click quick-adds. Yahoo symbols are opaque (^KS11, 005930.KS); the label
// map lets both the buttons and the chips read in Korean.
const PRESETS=[['코스피','^KS11'],['삼성전자','005930.KS'],['SK하이닉스','000660.KS'],
 ['나스닥','^IXIC'],['S&P','^GSPC'],['비트코인','BTC-USD']];
const LABELS=Object.fromEntries(PRESETS.map(([n,s])=>[s,n]));
// TK / PICK are client-owned once loaded: they are only pushed on 전송, so a
// poll must never overwrite what the user is composing.
function rendertk(){$('chips').innerHTML=
 TK.map((t,i)=>'<span class=chip>'+(LABELS[t]?LABELS[t]+' <span style=color:#5c6773>'+t+'</span>':t)
  +'<b onclick="deltk('+i+')">×</b></span>').join('');renderpre()}
function renderpre(){$('presets').innerHTML=
 PRESETS.map(([n,s])=>'<button onclick="addsym(\''+s+'\')"'+(TK.includes(s)?' disabled':'')+'>+ '+n+'</button>').join('')}
function addsym(v){v=v.trim().toUpperCase();
 if(v&&!TK.includes(v)){TK.push(v);rendertk();dirty('s')}}
function addtk(){let e=$('tk');addsym(e.value);e.value=''}
function deltk(i){TK.splice(i,1);rendertk();dirty('s')}
function pick(n){PICK=(PICK===n?'':n);
 document.querySelectorAll('#th img').forEach(i=>i.classList.toggle('sel',i.dataset.n===PICK));
 dirty('s')}

// ---- monitor ----
function fmt(s){s=+s;if(!s)return '—';let h=s/3600|0,m=(s%3600)/60|0;return h?h+'h '+m+'m':m+'m '+(s%60|0)+'s'}
async function tick(){try{let s=await(await fetch('/status')).json();
 $('status').innerHTML='<span style="color:'+(s.online?'#5fbf7f':'#bf6b6b')+'">●</span> '+(s.online?'online':'offline')+'  '+s.host;
 CUR=s.current;marksel();
 heap.textContent=s.heap?((s.heap/1024).toFixed(1)+' KB'):'—';
 rssi.textContent=s.rssi!=null?(Math.round(s.rssi)+' dBm'):'—';
 uptime.textContent=fmt(s.uptime);
 // Only refresh the global fields while they are clean — otherwise a poll would
 // wipe edits the user has not saved yet.
 if(!$('gsave').classList.contains('dirty')){
  if(document.activeElement!==$('dev'))$('dev').value=s.host;
  CB=s.bits||16;$('dith').checked=!!s.dither;markcm()}
}catch(e){}}
function drawHeat(t){let c=$('heat'),x=c.getContext('2d');x.clearRect(0,0,240,240);
 if(!t.grid||!$('heaton').checked)return;
 let cw=240/t.gw,ch=240/t.gh;x.fillStyle='rgba(217,119,87,.5)';
 for(let i=0;i<t.grid.length;i++)if(t.grid[i]){x.fillRect((i%t.gw)*cw,((i/t.gw)|0)*ch,cw,ch)}}
async function mon(){$('mirror').src='/frame.jpg?'+Date.now();
 try{let t=await(await fetch('/telemetry')).json();
  let fresh=t.ts&&(Date.now()/1000-t.ts<2);
  drawHeat(fresh?t:{});
  mstats.textContent=fresh?(t.fps+' fps · '+t.blits+' patches · '+t.kbps+' KB/s'):'정지됨';
 }catch(e){}}
async function thumbs(){let n=await(await fetch('/stickers')).json();
 $('th').innerHTML=n.map(x=>'<img data-n="'+x+'" src="/thumb?name='+x+'" onclick="pick(\''+x+'\')">').join('')}
$('tk').addEventListener('keydown',e=>{if(e.key=='Enter')addtk()});
$('br').addEventListener('change',()=>dirty('g'));
fetch('/status').then(r=>r.json()).then(s=>{
 TK=s.tickers||[];rendertk();$('rot').value=s.ticker_rotate||15;
 if(s.brightness!=null){$('br').value=s.brightness;bv.textContent=s.brightness+'%'}
 sel(s.current||'furnace');clean('s')});
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


APPLY = threading.Lock()    # two fast clicks must not interleave stop_all()/start()


def bg(fn, *a):
    threading.Thread(target=fn, args=a, daemon=True).start()


def apply_source(q):
    """Start the selected source with the settings sent alongside it.

    Settings arrive with the switch rather than on every keystroke: the pane is a
    draft until 전송, so this is the one place they are persisted and used.
    """
    src = q.get("src", "")
    if src == "stocks":
        c = cfg_mod.load()
        c["tickers"] = set_tickers(q.get("tickers", ""))
        try:
            c["ticker_rotate"] = max(3.0, float(q.get("rotate", 15)))
        except ValueError:
            pass
        cfg_mod.save(c)
        extra = [*c["tickers"], "--rotate", str(c["ticker_rotate"])]
    elif src == "stickers":
        name = q.get("pick", "")
        extra = [GIFDIR, *(["--pick", name] if name else [])]
    elif src == "video":
        extra = [q.get("path", "")]
        if not extra[0]:
            return
    else:
        extra = []
    with APPLY:
        stream.stop_all()
        if src in stream.SOURCES:
            # via stream.start so the detach flags stay in one place (start_new_session
            # is POSIX-only; Windows needs creationflags instead)
            stream.start(src, extra, host=HOST)


def apply_settings(q):
    """Persist + push the global settings in one go (the 저장 button)."""
    set_host(q.get("ip", ""))
    if "brightness" in q:
        try:
            v = max(1, min(100, int(q["brightness"])))
        except ValueError:
            v = None
        if v is not None:
            c = cfg_mod.load()
            c["brightness"] = v
            cfg_mod.save(c)
            dev_post(f"/light/backlight/turn_on?brightness={int(v * 255 / 100)}")
    # Colour depth is read by the running source from this file, not by the device.
    os.makedirs(TELEM, exist_ok=True)
    with open(MODE, "w") as f:
        json.dump({"bits": 8 if q.get("bits") == "8" else 16,
                   "dither": q.get("dither") in ("1", "true")}, f)


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
            c = cfg_mod.load()
            st.update(tickers=c["tickers"], ticker_rotate=c["ticker_rotate"],
                      brightness=c["brightness"])
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
        # Both handlers are slow (a source switch waits out a 3 s process teardown,
        # a device POST waits on a busy ESP8266), and the browser has nothing to do
        # with the result — so answer now and do the work on a worker thread.
        if p == "/apply":
            bg(apply_source, {k: v[0] for k, v in qs.items()})
        elif p == "/settings":
            bg(apply_settings, {k: v[0] for k, v in qs.items()})
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
