/**
 * The popup: one row per provider, each showing what the browser has and what
 * the panel has, with a button that moves the first into the second.
 *
 * The cookie value itself is never rendered — only its state and expiry. It
 * lives in a closure for the length of a click and goes to loopback or the
 * clipboard, nowhere else.
 */
import { PROVIDERS } from './lib/providers.js';
import { getCookie, describeExpiry, expiryText } from './lib/cookies.js';
import { sendKey, getStatus, panelUrl, setPanelUrl } from './lib/panel.js';

const rows = document.getElementById('rows');
const tpl = document.getElementById('row');

const COOKIE_ERR = {
  not_found: '쿠키가 없습니다 — 로그인 후 다시 열어주세요',
  invalid_format: '쿠키 형식이 예상과 다릅니다',
  unknown: '쿠키를 읽지 못했습니다',
};

for (const provider of PROVIDERS) render(provider);
initPanelField();

async function render(provider) {
  const el = tpl.content.firstElementChild.cloneNode(true);
  el.style.setProperty('--accent', provider.accent);
  el.querySelector('.label').textContent = provider.label;
  el.querySelector('.cookie').textContent = provider.cookieName;
  const dot = el.querySelector('.dot');
  const status = el.querySelector('.status');
  const result = el.querySelector('.result');
  const send = el.querySelector('.send');
  const copy = el.querySelector('.copy');
  rows.appendChild(el);

  const cookie = await getCookie(provider);
  const exp = cookie.ok ? describeExpiry(cookie.expirationDate) : null;
  const expired = exp?.expired;

  if (!cookie.ok || expired) {
    dot.className = 'dot bad';
    status.textContent = expired
      ? `쿠키가 만료되었습니다 · ${provider.hint}`
      : `${COOKIE_ERR[cookie.error] || COOKIE_ERR.unknown} · ${provider.hint}`;
    send.disabled = copy.disabled = true;
  } else {
    // Under two days left is worth flagging: the panel would start failing
    // mid-week with nothing on screen to explain why.
    dot.className = exp && exp.days < 2 ? 'dot warn' : 'dot ok';
    status.textContent = `쿠키 확인됨 · ${expiryText(cookie.expirationDate)}`;
    send.onclick = () => run(send, result, async () => {
      const j = await sendKey(provider, cookie.value);
      if (!j.ok) throw new Error(j.error || '패널이 거부했습니다');
      return '저장됨' + (j.plan ? ` (${j.plan})` : j.org_id ? ` (org ${j.org_id.slice(0, 8)}…)` : '');
    });
    copy.onclick = () => run(copy, result, async () => {
      await navigator.clipboard.writeText(cookie.value);
      return '클립보드에 복사했습니다';
    });
  }

  // What the panel already has, so a row says whether sending is even needed.
  try {
    const st = await getStatus(provider);
    const parts = [st.saved ? '패널: 저장됨' : '패널: 저장된 키 없음'];
    if (st.found && st.primary != null) {
      parts.push(`5h ${Math.round(st.primary)}% / 주간 ${Math.round(st.secondary)}%`);
    }
    status.textContent += `\n${parts.join(' · ')}`;
    status.style.whiteSpace = 'pre-line';
  } catch {
    status.textContent += '\n패널에 연결할 수 없습니다';
    status.style.whiteSpace = 'pre-line';
  }
}

/** Run an action with the button disabled, and report the outcome in the row. */
async function run(button, out, fn) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = '…';
  out.className = 'result';
  out.textContent = '';
  try {
    out.textContent = await fn();
    out.className = 'result ok';
  } catch (e) {
    out.textContent = e?.message || '실패했습니다';
    out.className = 'result bad';
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function initPanelField() {
  const input = document.getElementById('panel');
  const note = document.getElementById('panelnote');
  const base = note.textContent;
  input.value = await panelUrl();
  document.getElementById('panelsave').onclick = async () => {
    try {
      input.value = await setPanelUrl(input.value);
      note.className = 'note';
      note.textContent = '저장했습니다. 팝업을 다시 열면 적용됩니다.';
    } catch (e) {
      note.className = 'note bad';
      note.textContent = e.message;
      setTimeout(() => { note.className = 'note'; note.textContent = base; }, 3000);
    }
  };
}
