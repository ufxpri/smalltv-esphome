/**
 * Toolbar badge: the worse of the two session states, so an expiring cookie is
 * visible before the SmallTV starts showing SESSION EXPIRED.
 *
 *   (no badge)  both sessions healthy
 *   orange !    one expires within two days
 *   red !       one is missing or already expired
 *
 * This worker only reads cookies. Nothing here sends anything anywhere.
 */
import { PROVIDERS } from './lib/providers.js';
import { getCookie, describeExpiry } from './lib/cookies.js';

const BADGE = {
  fresh: { text: '', color: '#5fbf7f' },
  stale: { text: '!', color: '#e0a33f' },
  bad: { text: '!', color: '#bf6b6b' },
};
const RANK = { fresh: 0, stale: 1, bad: 2 };

async function stateOf(provider) {
  const c = await getCookie(provider);
  if (!c.ok) return 'bad';
  const e = describeExpiry(c.expirationDate);
  if (!e) return 'fresh';
  if (e.expired) return 'bad';
  return e.days < 2 ? 'stale' : 'fresh';
}

async function updateBadge() {
  const states = await Promise.all(PROVIDERS.map(stateOf));
  const worst = states.reduce((a, b) => (RANK[b] > RANK[a] ? b : a), 'fresh');
  const badge = BADGE[worst];
  try {
    await chrome.action.setBadgeBackgroundColor({ color: badge.color });
    await chrome.action.setBadgeText({ text: badge.text });
  } catch { /* no action in some contexts */ }
}

// Only react to the two cookies we care about — chrome.cookies.onChanged fires
// for every cookie in the browser, and re-reading on each would be absurd.
const WATCHED = new Set(PROVIDERS.map((p) => p.cookieName));
chrome.cookies.onChanged.addListener(({ cookie }) => {
  if (WATCHED.has(cookie.name) || [...WATCHED].some((n) => cookie.name.startsWith(`${n}.`))) {
    updateBadge();
  }
});

chrome.runtime.onStartup.addListener(updateBadge);
chrome.runtime.onInstalled.addListener(updateBadge);
updateBadge();
