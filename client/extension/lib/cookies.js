/**
 * Reading one provider's session cookie. Single source of truth for both the
 * popup and the background badge.
 *
 * The cookie never leaves this extension except through `lib/panel.js`, which
 * only ever talks to loopback.
 */

/**
 * @typedef {Object} CookieResult
 * @property {boolean} ok
 * @property {string}  [value]
 * @property {number}  [expirationDate]  seconds since epoch, from chrome.cookies
 * @property {'not_found'|'invalid_format'|'unknown'} [error]
 * @property {string}  [message]
 */

/**
 * The provider's session cookie, reassembled if the browser split it.
 * @param {{cookieUrl: string, cookieName: string, looksValid: (v: string) => boolean}} provider
 * @returns {Promise<CookieResult>}
 */
export async function getCookie(provider) {
  const { cookieUrl, cookieName } = provider;
  let cookie, chunks;
  try {
    cookie = await chrome.cookies.get({ url: cookieUrl, name: cookieName });
    if (!cookie?.value) chunks = await readChunked(cookieUrl, cookieName);
  } catch (err) {
    return { ok: false, error: 'unknown', message: err?.message ?? 'unknown error' };
  }

  const value = cookie?.value || chunks?.value;
  if (!value) return { ok: false, error: 'not_found' };
  if (!provider.looksValid(value)) return { ok: false, error: 'invalid_format' };
  return { ok: true, value, expirationDate: cookie?.expirationDate ?? chunks?.expirationDate };
}

/**
 * NextAuth splits a session cookie larger than ~4 KB into `<name>.0`, `<name>.1`,
 * … so a plain get() comes back empty on exactly the accounts whose token is
 * biggest. Stitch the numbered parts back together in order.
 */
async function readChunked(url, name) {
  const all = await chrome.cookies.getAll({ url });
  const parts = all
    .filter((c) => c.name.startsWith(`${name}.`))
    .map((c) => ({ n: Number(c.name.slice(name.length + 1)), c }))
    .filter((p) => Number.isInteger(p.n))
    .sort((a, b) => a.n - b.n);
  if (!parts.length) return null;
  return {
    value: parts.map((p) => p.c.value).join(''),
    // The chunks share one expiry; any of them answers for the whole.
    expirationDate: parts[0].c.expirationDate,
  };
}

/**
 * Remaining validity of a chrome.cookies expirationDate.
 * @returns {{expired: boolean, hours: number, days: number} | null}
 */
export function describeExpiry(expirationDate) {
  if (!expirationDate) return null;
  const secs = expirationDate - Date.now() / 1000;
  if (secs <= 0) return { expired: true, hours: 0, days: 0 };
  const hours = Math.floor(secs / 3600);
  return { expired: false, hours, days: Math.floor(hours / 24) };
}

/** Short Korean summary of an expiry, for the popup. */
export function expiryText(expirationDate) {
  const e = describeExpiry(expirationDate);
  if (!e) return '만료 정보 없음';
  if (e.expired) return '만료됨';
  if (e.days >= 1) return `${e.days}일 남음`;
  return `${e.hours}시간 남음`;
}
