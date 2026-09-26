// One live connection per tab (Server-Sent Events). The server counts open tabs
// with it, which lets the one-click launch stop once the last tab is closed; the
// page uses it to notice a stopped server. EventSource reconnects by itself, so a
// reload, a short network blip, or a restarted Riffle heal on their own.

export const connection = $state({ down: false, autoExit: false });

let downTimer;

export function connect() {
  const es = new EventSource('/api/events');
  es.onmessage = (e) => {
    try {
      connection.autoExit = !!JSON.parse(e.data).auto_exit;
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
