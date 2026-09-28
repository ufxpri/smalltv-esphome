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
import logging
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
import claudeusage  # noqa: E402
import codexusage  # noqa: E402
import config as cfg_mod  # noqa: E402
import logs  # noqa: E402
import stream  # noqa: E402
from smalltv_stream import TELEM_DIR as TELEM  # noqa: E402

_ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]
# The panel is the settings UI, so the saved config is the source of truth for
# which device to talk to; an explicit argv host still wins for one-off runs.
HOST = _ARGS[0] if _ARGS else cfg_mod.load()["device_ip"]
NO_BROWSER = "--no-browser" in sys.argv     # the widget launches us at login
GIFDIR = stream.gif_dir()
VIDDIR = stream.video_dir()
MODE = os.path.join(TELEM, "mode.json")
PORT = 8787
_LOCAL_HOSTS = {f"{h}:{PORT}" for h in ("127.0.0.1", "localhost", "[::1]")}
_LOCAL_ORIGINS = {f"http://{h}" for h in _LOCAL_HOSTS}
LOG = logging.getLogger("panel")
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
.src[data-k=claude].on{background:#c0552f}.src[data-k=codex].on{background:#12806a}
.src[data-k=usage].on{background:linear-gradient(100deg,#c0552f 45%,#12806a 55%)}
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
.thumbs .none{display:flex;align-items:center;justify-content:center;aspect-ratio:1;border-radius:9px;background:#22242b;cursor:pointer;border:2px solid transparent;font-size:11px;color:#9aa4af}
.thumbs .none:hover{border-color:#d97757}.thumbs .none.sel{border-color:#d97757}
.seg{display:flex;gap:8px;align-items:center}
.seg button{border:0;border-radius:10px;background:#22242b;color:#e7e2da;padding:9px 14px;cursor:pointer;font-size:13px}
.seg button.on{background:#3a5a9d;color:#fff}
.seg label{font-size:13px;color:#9aa4b0;margin-left:auto;display:flex;gap:6px;align-items:center;cursor:pointer}
.seg input{flex:1;min-width:0;border:0;border-radius:10px;background:#22242b;color:#e7e2da;padding:9px 12px;font-size:13px;font-family:ui-monospace,monospace}
.seg input[type=number]{flex:none;width:80px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.chip{background:#22242b;border-radius:8px;padding:6px 8px 6px 11px;font-size:13px;font-family:ui-monospace,monospace;display:flex;gap:7px;align-items:center}
.chip b{color:#7c8b99;cursor:pointer;font-weight:400}.chip b:hover{color:#ff6b6b}
#vl .chip{cursor:pointer;border:2px solid transparent}#vl .chip:hover{border-color:#3a5a9d}
#vl .chip.sel{border-color:#d97757}
.presets{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.presets button{border:0;border-radius:8px;background:#1b2530;color:#9fc4e0;padding:6px 11px;cursor:pointer;font-size:13px}
.presets button:hover{background:#243444}.presets button:disabled{opacity:.4;cursor:default}
.apply{width:100%;border:0;border-radius:12px;background:#d97757;color:#fff;font-size:15px;font-weight:600;padding:13px 0;margin-top:14px;cursor:pointer}
.apply:hover{filter:brightness(1.1)}
.apply.dirty::after{content:' •'}
.hint{font-size:12px;color:#7c8b99;margin-top:8px;text-align:center;min-height:15px}
.pane{display:none}.pane.show{display:block}
.logbar{display:flex;gap:7px;align-items:center;margin-bottom:8px}
.logbar select{flex:1;min-width:0;border:0;border-radius:9px;background:#22242b;color:#e7e2da;padding:8px 10px;font-size:12.5px}
#logbox{height:190px;overflow:auto;background:#0b0c0f;border-radius:10px;padding:9px 11px;
 font-family:ui-monospace,monospace;font-size:11px;line-height:1.55;white-space:pre-wrap;word-break:break-all;color:#9aa4b0}
#logbox .W{color:#e0a33f}#logbox .E{color:#e07a6b}#logbox .C{color:#ff6b6b;font-weight:600}#logbox .D{color:#5c6773}
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
 <div class=sec>로그 상세도 — DEBUG는 폴링 수치까지 남깁니다 (다음 실행부터 적용)</div>
 <div class=seg>
  <button id=lvINFO onclick="lv('INFO')">보통</button>
  <button id=lvDEBUG onclick="lv('DEBUG')">상세</button>
  <button id=lvWARNING onclick="lv('WARNING')">경고만</button>
 </div>
 <div class=sec>기기 주소 — IP 또는 호스트명</div>
 <div class=seg><input id=dev type=text spellcheck=false placeholder="192.168.0.10" oninput="dirty('g')"></div>
 <button class=apply id=gsave onclick="saveGlobal()">저장</button>
 <div class=hint id=ghint></div>
</div>

<div class=card>
 <div class=sec>로그</div>
 <div class=logbar>
  <select id=logsel onchange="loadlog()"></select>
  <button onclick="loadlog()">새로고침</button>
  <label style="font-size:12px;color:#7c8b99;display:flex;gap:5px;align-items:center">
   <input type=checkbox id=logfollow checked> 따라가기</label>
 </div>
 <div id=logbox>…</div>
</div>

<div class=card>
 <div class=sec>소스 — 고른 뒤 아래 전송을 누르면 적용됩니다</div>
 <div class=grid>
  <button class=src data-k=furnace onclick="sel('furnace')">🔥 용광로</button>
  <button class=src data-k=stickers onclick="sel('stickers')">😺 스티커</button>
  <button class=src data-k=stocks onclick="sel('stocks')">📈 주식</button>
  <button class=src data-k=sectors onclick="sel('sectors')">🗺️ 섹터</button>
  <button class=src data-k=claude onclick="sel('claude')">🤖 Claude</button>
  <button class=src data-k=codex onclick="sel('codex')">⌨️ Codex</button>
  <button class=src data-k=usage onclick="sel('usage')">🔁 번갈아</button>
  <button class=src data-k=video onclick="sel('video')">🎥 영상</button>
  <button class=src data-k=off onclick="sel('off')">⏻ 끄기</button>
 </div>

 <div class=pane data-p=furnace><div class=sec>CPU 부하를 용광로 불꽃으로 그립니다. 설정 없음.</div></div>
 <div class=pane data-p=sectors><div class=sec>S&amp;P 섹터 히트맵. 설정 없음.</div></div>
 <div class=pane data-p=claude>
  <div class=sec>Claude 사용량: 5시간 창의 소진 추이와 100% 도달 예측. claude.ai 한도 API에서 읽습니다.</div>
  <div class=sec id=ckstat>세션 키 확인 중…</div>
  <div class=seg><input id=cksk type=password spellcheck=false placeholder="세션 키  sk-ant-sid02-…"></div>
  <div class=seg><input id=ckorg type=text spellcheck=false placeholder="조직 ID (비우면 자동 감지)"></div>
  <div class=seg><button onclick="savekey()">세션 키 저장</button></div>
  <div class=hint id=ckhint></div>
  <div class=sec>우하단 박스 — 기본은 Claude 마스코트, GIF로 교체 가능</div>
  <div class=thumbs id=claudegif></div>
  <div class=seg><button onclick="post('/burst')">💥 폭발 테스트</button></div>
 </div>
 <div class=pane data-p=codex>
  <div class=sec>Codex 사용량: 같은 화면을 Codex 색으로. chatgpt.com 사용량 API에서 읽으므로
   Codex를 다른 컴퓨터에서 돌려도 그대로 보입니다.</div>
  <div class=sec id=cxstat>세션 쿠키 확인 중…</div>
  <div class=seg><input id=cxsk type=password spellcheck=false
   placeholder="__Secure-next-auth.session-token 값"></div>
  <div class=seg><button onclick="savecx()">세션 쿠키 저장</button></div>
  <div class=hint id=cxhint>chatgpt.com → 개발자도구 → Application → Cookies → __Secure-next-auth.session-token</div>
  <div class=sec>우하단 박스 — 기본은 터미널 애니메이션, GIF로 교체 가능</div>
  <div class=thumbs id=codexgif></div>
  <div class=seg><button onclick="post('/burst')">💥 폭발 테스트</button></div>
 </div>
 <div class=pane data-p=usage>
  <div class=sec>Claude와 Codex 사용량 화면을 한 연결에서 번갈아 보여줍니다. 각 화면의 키·GIF
   설정은 위의 Claude / Codex 창에서 그대로 쓰입니다.</div>
  <div class=sec id=uxstat>키 상태 확인 중…</div>
  <div class=sec>전환 간격 (초)</div>
  <div class=seg><input id=urot type=number min=5 max=600 step=1 value=20 oninput="dirty('s')"></div>
  <div class=sec>키가 없거나 만료된 화면은 건너뜁니다 — 둘 다 못 그릴 때만 안내 화면이 번갈아 뜹니다.</div>
 </div>
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
  <div class=sec>영상 업로드 — mp4 / gif / webm 등, ffmpeg가 읽는 것 전부</div>
  <div class=seg>
   <input id=vfile type=file accept="video/*,.gif,.webm,.mkv,.avi,.mov" style="display:none" onchange="upvid(this)">
   <button onclick="$('vfile').click()">📁 파일 선택…</button>
   <span id=vup style="font-size:12px;color:#7c8b99"></span>
  </div>
  <div class=sec>재생할 영상 — 하나를 골라 전송하세요</div>
  <div class=chips id=vl></div>
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
let LV='INFO';
function lv(v){LV=v;marklv();dirty('g')}
function marklv(){['INFO','DEBUG','WARNING'].forEach(v=>$('lv'+v).classList.toggle('on',LV==v))}
function saveGlobal(){
 let q='/settings?bits='+CB+'&dither='+($('dith').checked?1:0)+'&brightness='+$('br').value
       +'&log_level='+LV;
 let ip=$('dev').value.trim();if(ip)q+='&ip='+encodeURIComponent(ip);
 post(q);clean('g');flash($('ghint'),'저장했습니다')}

// ---- claude session key (localhost-only; key is never echoed back) ----
function loadck(){fetch('/claude_status').then(r=>r.json()).then(s=>{
 $('ckstat').textContent=s.saved
  ?('세션 키 저장됨 · org '+(s.org_id||'').slice(0,8)+'… · …'+s.key_hint)
  :'세션 키가 설정되지 않았습니다';}).catch(e=>{})}
function savekey(){
 let sk=$('cksk').value.trim();
 if(!sk){flash($('ckhint'),'세션 키를 입력하세요');return}
 let body='session_key='+encodeURIComponent(sk)+'&org_id='+encodeURIComponent($('ckorg').value.trim());
 $('ckhint').textContent='저장·확인 중…';
 fetch('/claude_key',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body})
  .then(r=>r.json()).then(j=>{
   if(j.ok){$('cksk').value='';flash($('ckhint'),'저장됨 (org '+(j.org_id||'').slice(0,8)+'…)');loadck()}
   else{flash($('ckhint'),'실패: '+(j.error||'알 수 없는 오류'))}
  }).catch(e=>{flash($('ckhint'),'요청 실패')})}

// ---- codex usage (no key: it reads Codex's own session logs) ----
function age(s){if(s<5400)return Math.round(s/60)+'분';
 if(s<172800)return Math.round(s/3600)+'시간';return Math.round(s/86400)+'일'}
function loadcx(){fetch('/codex_status').then(r=>r.json()).then(s=>{
 let key=s.saved?('쿠키 저장됨 · …'+s.key_hint):'쿠키 없음 (로컬 로그로 대체)';
 let read=s.found
  ?(' · '+(s.source=='api'?'API':'로컬 '+age(s.age_s)+' 전')
    +' · 5h '+Math.round(s.primary)+'% / 주간 '+Math.round(s.secondary)+'%')
  :(' · 읽기 실패: '+(s.error||'데이터 없음'));
 $('cxstat').textContent=key+read;}).catch(e=>{})}
function savecx(){
 let sk=$('cxsk').value.trim();
 if(!sk){flash($('cxhint'),'세션 쿠키를 입력하세요');return}
 $('cxhint').textContent='저장·확인 중…';
 fetch('/codex_key',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
   body:'session_token='+encodeURIComponent(sk)})
  .then(r=>r.json()).then(j=>{
   if(j.ok){$('cxsk').value='';flash($('cxhint'),'저장됨'+(j.plan?' ('+j.plan+')':''));loadcx()}
   else{flash($('cxhint'),'실패: '+(j.error||'알 수 없는 오류'))}
  }).catch(e=>{flash($('cxhint'),'요청 실패')})}

function loadux(){Promise.all([
  fetch('/claude_status').then(r=>r.json()).catch(e=>({})),
  fetch('/codex_status').then(r=>r.json()).catch(e=>({}))]).then(([c,x])=>{
 $('uxstat').textContent='Claude '+(c.saved?'키 있음':'키 없음')
   +' · Codex '+(x.saved?'키 있음':(x.found?'키 없음 (로컬 로그)':'키 없음'));}).catch(e=>{})}

// ---- the usage screens' mascot box, one picker per screen keyed by config name ----
const UGIF={claude:'',codex:''};
function loadugif(k){fetch('/stickers').then(r=>r.json()).then(n=>{
 let cur=UGIF[k],mascot=k=='codex'?'터미널':'마스코트';
 let none='<div class="thumb none'+(cur?'':' sel')+'" data-n="" onclick="pickugif(\''+k+'\',\'\')">'+mascot+'</div>';
 $(k+'gif').innerHTML=none+n.map(x=>'<img data-n="'+x+'" class="'+(x==cur?'sel':'')
  +'" src="/thumb?name='+x+'" onclick="pickugif(\''+k+'\',\''+x+'\')">').join('')}).catch(e=>{})}
function pickugif(k,n){UGIF[k]=n;post('/'+k+'_gif?name='+encodeURIComponent(n));
 document.querySelectorAll('#'+k+'gif [data-n]').forEach(e=>e.classList.toggle('sel',e.dataset.n===n))}

// ---- source selection (applied only by 전송) ----
let SEL='furnace',CUR=null,TK=[],PICK='';
function sel(k){SEL=k;marksel();
 document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('show',p.dataset.p===k));
 // The codex reading is a file scan, so refresh it when its pane opens rather
 // than on the 3 s status poll.
 if(k=='codex')loadcx();
 if(k=='usage')loadux();
 $('send').textContent=k=='off'?'중지':'전송';dirty('s')}
function marksel(){document.querySelectorAll('.src').forEach(b=>{
 b.classList.toggle('sel',b.dataset.k===SEL);b.classList.toggle('on',b.dataset.k===CUR)})}
function sendSrc(){
 let q='/apply?src='+SEL;
 if(SEL=='stocks')q+='&tickers='+encodeURIComponent(TK.join(','))+'&rotate='+($('rot').value||15);
 if(SEL=='usage')q+='&rotate='+($('urot').value||20);
 if(SEL=='stickers')q+='&pick='+encodeURIComponent(PICK);
 if(SEL=='video'){if(!VSEL){flash($('shint'),'영상을 업로드하거나 목록에서 선택하세요');return}
  q+='&name='+encodeURIComponent(VSEL)}
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
// ---- uploaded videos ----
let VSEL='';
async function vids(){try{let n=await(await fetch('/videos')).json();
 $('vl').innerHTML=n.length?n.map(x=>'<span class="chip'+(x===VSEL?' sel':'')
  +'" onclick="pickv(\''+x.replace(/'/g,"\\'")+'\')">'+x
  +' <b onclick="event.stopPropagation();delv(\''+x.replace(/'/g,"\\'")+'\')">×</b></span>').join('')
  :'<span style="font-size:12px;color:#7c8b99">업로드된 영상이 없습니다</span>'}catch(e){}}
function pickv(n){VSEL=n;vids();dirty('s')}
async function delv(n){await fetch('/del_video?name='+encodeURIComponent(n),{method:'POST'}).catch(e=>{});
 if(VSEL===n)VSEL='';vids()}
async function upvid(inp){let f=inp.files[0];if(!f)return;
 $('vup').textContent='업로드 중… '+(f.size/1048576).toFixed(1)+' MB';
 try{
  let r=await fetch('/upload_video?name='+encodeURIComponent(f.name),{method:'POST',body:f});
  let j=await r.json();
  if(j.ok){VSEL=j.name;vids();dirty('s');$('vup').textContent='완료 — 전송을 누르면 재생됩니다'}
  else $('vup').textContent='실패: '+(j.error||'알 수 없는 오류');
 }catch(e){$('vup').textContent='업로드 실패'}
 inp.value=''}

function pick(n){PICK=(PICK===n?'':n);
 document.querySelectorAll('#th img').forEach(i=>i.classList.toggle('sel',i.dataset.n===PICK));
 dirty('s')}

// ---- logs ----
// The level letter logging writes (I/W/E/D) is the first field, so colouring is
// a one-character test rather than a parse.
let LOGN='';
async function loglist(){try{
 let n=await(await fetch('/logs')).json();
 if(!n.length)return;
 if(!LOGN)LOGN=n[0];
 $('logsel').innerHTML=n.map(x=>'<option'+(x==LOGN?' selected':'')+'>'+x+'</option>').join('');
}catch(e){}}
async function loadlog(){
 LOGN=$('logsel').value||LOGN;if(!LOGN)return;
 try{
  let t=await(await fetch('/log?name='+encodeURIComponent(LOGN)+'&n=300')).text();
  let box=$('logbox'),stick=$('logfollow').checked;
  box.innerHTML=t.split('\n').map(l=>{
   let m=l.match(/^\S+ \S+ ([DIWEC]) /);
   let esc=l.replace(/&/g,'&amp;').replace(/</g,'&lt;');
   return m?'<span class="'+m[1]+'">'+esc+'</span>':esc;}).join('\n');
  if(stick)box.scrollTop=box.scrollHeight;
 }catch(e){$('logbox').textContent='로그를 읽지 못했습니다'}}

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
  CB=s.bits||16;$('dith').checked=!!s.dither;markcm();
  LV=s.log_level||'INFO';marklv()}
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
 $('urot').value=s.usage_rotate||20;
 if(s.brightness!=null){$('br').value=s.brightness;bv.textContent=s.brightness+'%'}
 UGIF.claude=s.claude_gif||'';UGIF.codex=s.codex_gif||'';loadugif('claude');loadugif('codex');
 sel(s.current||'furnace');clean('s')});
thumbs();vids();tick();loadck();loadcx();
loglist().then(loadlog);setInterval(()=>{loglist();loadlog()},5000);
setInterval(tick,3000);setInterval(mon,250);
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
    elif src == "usage":
        c = cfg_mod.load()
        try:
            c["usage_rotate"] = max(5.0, float(q.get("rotate", 20)))
        except ValueError:
            pass
        cfg_mod.save(c)
        extra = ["--rotate", str(c["usage_rotate"])]
    elif src == "stickers":
        name = q.get("pick", "")
        extra = [GIFDIR, *(["--pick", name] if name else [])]
    elif src == "video":
        name = os.path.basename(q.get("name", ""))
        # `path` survives for one-off use; strip the quotes Windows' "copy as
        # path" wraps around it — quoted, ffmpeg fails with "Invalid argument".
        path = (os.path.join(VIDDIR, name) if name
                else q.get("path", "").strip().strip('"').strip("'"))
        if not path or not os.path.isfile(path):
            return
        extra = [path]
    else:
        extra = []
    with APPLY:
        stream.stop_all()
        if src in stream.SOURCES:
            # via stream.start so the detach flags stay in one place (start_new_session
            # is POSIX-only; Windows needs creationflags instead)
            stream.start(src, extra, host=HOST)


def set_usage_gif(key, name):
    """Persist which gif a usage screen shows (a name in gif_dir, or ''), where
    `key` is that screen's config key — "claude_gif" / "codex_gif"."""
    c = cfg_mod.load()
    c[key] = (name or "").strip()
    cfg_mod.save(c)


def fire_burst():
    """Bump the counter the usage sources poll, so one detonates a burst at once."""
    os.makedirs(TELEM, exist_ok=True)
    path = os.path.join(TELEM, "burst.json")
    try:
        with open(path) as f:
            n = int(json.load(f).get("n", 0))
    except Exception:
        n = 0
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"n": n + 1}, f)
    os.replace(tmp, path)


def apply_settings(q):
    """Persist + push the global settings in one go (the 저장 button)."""
    set_host(q.get("ip", ""))
    lvl = (q.get("log_level", "") or "").upper()
    if lvl in ("DEBUG", "INFO", "WARNING"):
        c = cfg_mod.load()
        if c["log_level"] != lvl:
            c["log_level"] = lvl
            cfg_mod.save(c)
            # Processes read the level at startup, so this lands on the next one.
            LOG.info("log level set to %s (applies to sources started from now on)", lvl)
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
        self._allow_extension()
        self.end_headers()
        self.wfile.write(body)

    def _allow_extension(self):
        """Let the companion Chrome extension read our answers.

        It posts the session cookies it reads from the browser to /claude_key
        and /codex_key, and needs the JSON back to say whether the key took.
        The header is echoed only for chrome-extension:// origins; web pages
        are turned away before this by _trusted(). A bogus key still can't
        overwrite a good one, since both key handlers validate against the
        provider first.
        """
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _trusted(self):
        """Refuse requests that didn't come from this machine's panel page,
        the extension, or a plain local client (the widget's probe, curl).

        Binding 127.0.0.1 stops other machines, not other *websites*: any page
        open in the user's browser can make that browser send to 127.0.0.1:8787.
        Host catches DNS rebinding (a page whose own domain was re-pointed here
        carries that domain in Host); Origin catches cross-site POSTs, which
        browsers always label. No Origin at all means a non-browser client.
        """
        host = self.headers.get("Host", "")
        if host not in _LOCAL_HOSTS:
            return False
        origin = self.headers.get("Origin")
        return (origin is None or origin in _LOCAL_ORIGINS
                or origin.startswith("chrome-extension://"))

    def _refuse(self):
        LOG.warning("refused %s %s (Host=%r Origin=%r)", self.command,
                    self.path.split("?")[0], self.headers.get("Host"),
                    self.headers.get("Origin"))
        self._send(403, "text/plain", b"forbidden")

    def do_OPTIONS(self):
        if not self._trusted():
            return self._refuse()
        # Form-urlencoded POSTs are CORS-simple and never preflight, so this is
        # only here so a future JSON body doesn't fail mysteriously.
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self._allow_extension()
        self.end_headers()

    def q(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def do_GET(self):
        if not self._trusted():
            return self._refuse()
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
            st.update(log_level=c["log_level"],
                      tickers=c["tickers"], ticker_rotate=c["ticker_rotate"],
                      brightness=c["brightness"], claude_gif=c["claude_gif"],
                      codex_gif=c["codex_gif"], usage_rotate=c["usage_rotate"])
            self._send(200, "application/json", json.dumps(st).encode())
        elif p == "/claude_status":
            self._send(200, "application/json", json.dumps(claudeusage.secret_status()).encode())
        elif p == "/codex_status":
            self._send(200, "application/json", json.dumps(codexusage.status()).encode())
        elif p == "/logs":
            self._send(200, "application/json", json.dumps(logs.names()).encode())
        elif p == "/log":
            q = self.q()
            name = os.path.basename(q.get("name", [""])[0])
            try:
                n = max(10, min(2000, int(q.get("n", ["300"])[0])))
            except ValueError:
                n = 300
            body = "\n".join(logs.tail(name, n)) if name else ""
            self._send(200, "text/plain; charset=utf-8", body.encode())
        elif p == "/telemetry":
            self._send(200, "application/json", read_file(os.path.join(TELEM, "stat.json")) or b"{}")
        elif p == "/frame.jpg":
            img = read_file(os.path.join(TELEM, "frame.jpg"))
            self._send(200, "image/jpeg", img) if img else self._send(404, "text/plain", b"")
        elif p == "/videos":
            names = sorted(os.path.basename(x) for x in glob.glob(os.path.join(VIDDIR, "*"))
                           if os.path.isfile(x))
            self._send(200, "application/json", json.dumps(names).encode())
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

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode() if n > 0 else ""
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def do_POST(self):
        if not self._trusted():
            return self._refuse()
        p = urllib.parse.urlparse(self.path).path
        qs = self.q()
        # The session key rides in the POST body (not the URL) so it stays out of
        # any request line; this one answers with the result (detected org / error)
        # rather than fire-and-forget, so the browser can confirm the key took.
        if p == "/codex_key":
            b = self._body()
            try:
                plan = codexusage.save_secret(b.get("session_token", ""))
                self._send(200, "application/json", json.dumps({"ok": True, "plan": plan}).encode())
            except Exception as e:
                self._send(200, "application/json",
                           json.dumps({"ok": False, "error": str(e).split(chr(10))[0][:120]}).encode())
            return
        if p == "/claude_key":
            b = self._body()
            try:
                org = claudeusage.save_secret(b.get("session_key", ""), b.get("org_id", ""))
                self._send(200, "application/json", json.dumps({"ok": True, "org_id": org}).encode())
            except Exception as e:
                self._send(200, "application/json",
                           json.dumps({"ok": False, "error": str(e).split(chr(10))[0][:120]}).encode())
            return
        # The body is the raw file (no multipart — fetch posts the File object
        # straight), so it just streams to disk. Answers with the result so the
        # browser can select the new file or show why it was refused.
        if p == "/upload_video":
            name = os.path.basename(qs.get("name", [""])[0]).strip()
            n = int(self.headers.get("Content-Length") or 0)
            err = None
            if not name:
                err = "파일 이름이 없습니다"
            elif n <= 0:
                err = "빈 파일입니다"
            elif n > 512 * 1024 * 1024:
                err = "512 MB를 넘습니다"
            if err:
                self._send(200, "application/json", json.dumps({"ok": False, "error": err}).encode())
                return
            dst = os.path.join(VIDDIR, name)
            tmp = dst + ".part"
            try:
                with open(tmp, "wb") as f:
                    left = n
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            raise IOError("연결이 끊겼습니다")
                        f.write(chunk)
                        left -= len(chunk)
                os.replace(tmp, dst)
                self._send(200, "application/json", json.dumps({"ok": True, "name": name}).encode())
            except Exception as e:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                self._send(200, "application/json",
                           json.dumps({"ok": False, "error": str(e)[:120]}).encode())
            return
        if p == "/del_video":
            name = os.path.basename(qs.get("name", [""])[0])
            if name:
                try:
                    os.remove(os.path.join(VIDDIR, name))
                except OSError:
                    pass
            self._send(200, "text/plain", b"ok")
            return
        # Both handlers are slow (a source switch waits out a 3 s process teardown,
        # a device POST waits on a busy ESP8266), and the browser has nothing to do
        # with the result — so answer now and do the work on a worker thread.
        if p == "/apply":
            bg(apply_source, {k: v[0] for k, v in qs.items()})
        elif p == "/settings":
            bg(apply_settings, {k: v[0] for k, v in qs.items()})
        elif p in ("/claude_gif", "/codex_gif"):
            bg(set_usage_gif, p[1:], qs.get("name", [""])[0])
        elif p == "/burst":
            fire_burst()
        self._send(200, "text/plain", b"ok")


def dev_post(path):
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://{HOST}{path}", method="POST"), timeout=4)
    except Exception:
        pass


class Panel(ThreadingHTTPServer):
    # Windows' SO_REUSEADDR is not POSIX's: a SECOND process can bind a port that
    # is already bound and listen on it happily. Measured here: both sockets bind,
    # and the FIRST one keeps receiving the connections — so the duplicate becomes
    # a process that is alive, looks started, and serves nobody. It is not what
    # takes the panel down, but it is the factory that manufactures the orphans
    # widget/panel.py then has to reason about. Refuse the duplicate bind and let
    # the second instance die loudly instead.
    # POSIX keeps reuse on: there a duplicate LISTEN bind is refused anyway, and
    # turning it off would make a restart trip over the old socket's TIME_WAIT.
    allow_reuse_address = sys.platform != "win32"

    def handle_error(self, request, client_address):
        """A browser that navigates away mid-response makes the socket raise, and
        the default handler prints a full traceback for it. 78 of panel.log's
        first 85 tracebacks were exactly that — noise that buried the real ones.
        Disconnects go to DEBUG as one line; anything else is still a traceback.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            LOG.debug("client %s hung up: %s", client_address[0], exc)
            return
        LOG.exception("error handling request from %s", client_address[0])


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # cp949 consoles can't encode our logs
    except Exception:
        pass
    logs.setup("panel")
    # Bind before starting the poller: a duplicate instance should die without
    # having touched the device — it has ~20 KB of heap and one stream client.
    try:
        srv = Panel(("127.0.0.1", PORT), Handler)
    except OSError as e:
        # Loud and fatal on purpose — see Panel.allow_reuse_address.
        LOG.error("port %d is already in use, not starting (%s)", PORT, e)
        return 1
    threading.Thread(target=poller, daemon=True).start()
    url = f"http://localhost:{PORT}"
    LOG.info("control panel on %s (device %s)", url, HOST)
    if not NO_BROWSER:      # the widget starts us at login; don't pop a tab every boot
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
