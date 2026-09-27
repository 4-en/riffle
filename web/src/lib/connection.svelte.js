// One live connection per tab (Server-Sent Events). The server counts open tabs
// with it, which lets the one-click launch stop once the last tab is closed; the
// page uses it to notice a stopped server. EventSource reconnects by itself, so a
// reload, a short network blip, or a restarted Riffle heal on their own.

// model: the AI model's state on the server ('loading' | 'ready' | 'failed'); log: the
// standalone app's log file (null otherwise).
// profile: the active profile's slug; profileVersion bumps on switches and renames.
export const connection = $state({
  down: false,
  autoExit: false,
  model: 'ready',
  modelError: '',
  log: null,
  profile: null,
  profileVersion: 0,
});

let downTimer;

export function connect() {
  const es = new EventSource('/api/events');
  es.onmessage = (e) => {
    try {
      const d = JSON.parse(e.data);
      connection.autoExit = !!d.auto_exit;
      connection.model = d.model ?? 'ready';
      connection.modelError = d.model_error ?? '';
      connection.log = d.log ?? null;
      // Another tab switched the profile: everything shown belongs to the old one.
      if (connection.profile !== null && d.profile && d.profile !== connection.profile) reloadForProfile();
      connection.profile = d.profile ?? connection.profile;
      connection.profileVersion = d.profile_version ?? connection.profileVersion;
    } catch {}
  };
  es.onopen = () => {
    clearTimeout(downTimer);
    connection.down = false;
  };
  es.onerror = () => {
    clearTimeout(downTimer);
    // Brief drops (reload, sleep/wake) reconnect quickly; only report a lasting one.
    downTimer = setTimeout(() => {
      if (es.readyState !== EventSource.OPEN) connection.down = true;
    }, 3000);
  };
  return es;
}

/** Start over after a profile switch: flags on screen, undo history, selection, and
 * custom tag ids all belong to the old profile. Custom tag filters are dropped
 * (their ids differ between profiles); the rest of the view stays. */
export function reloadForProfile() {
  const url = new URL(location.href);
  url.searchParams.delete('ctags');
  url.searchParams.delete('xctags');
  location.replace(url);
}
