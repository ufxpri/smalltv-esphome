/**
 * The two usage screens, as the extension sees them: where the session cookie
 * lives and which panel endpoint takes it.
 *
 * This is the only place that names a cookie. Everything else works from these
 * records, which is what keeps the extension's reach exactly as wide as the two
 * entries below — see the manifest's host_permissions, which match them.
 */

export const PROVIDERS = [
  {
    id: 'claude',
    label: 'Claude',
    accent: '#d97757',
    cookieUrl: 'https://claude.ai',
    cookieName: 'sessionKey',
    // claude.ai keys are sk-ant-sid… — catching the wrong cookie here beats a
    // round trip to the panel and a rejection from the API.
    looksValid: (v) => v.startsWith('sk-ant-'),
    hint: 'claude.ai 에 로그인되어 있어야 합니다',
    keyPath: '/claude_key',
    keyField: 'session_key',
    statusPath: '/claude_status',
  },
  {
    id: 'codex',
    label: 'Codex',
    accent: '#1aaa86',
    cookieUrl: 'https://chatgpt.com',
    cookieName: '__Secure-next-auth.session-token',
    // An opaque NextAuth JWE: no stable prefix to check, so length is the only
    // cheap sanity test. The panel validates it for real before saving.
    looksValid: (v) => v.length > 40,
    hint: 'chatgpt.com 에 로그인되어 있어야 합니다',
    keyPath: '/codex_key',
    keyField: 'session_token',
    statusPath: '/codex_status',
  },
];

export const byId = (id) => PROVIDERS.find((p) => p.id === id);
