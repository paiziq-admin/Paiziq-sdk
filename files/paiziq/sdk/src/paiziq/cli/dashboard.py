"""Local dashboard deployment for the paiziq CLI (PZ-039).

`paiziq dashboard deploy` writes a self-contained static page;
`paiziq dashboard serve` hosts it with a read-only server-side proxy
(`/api/* → backend`), so the API key stays on the machine and never
reaches the browser. The hosted multi-tenant dashboard is a separate
workstream; this command covers local/dev deployments.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from .client import TransportFactory, default_transport_factory
from .config import load_config

_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Paiziq local dashboard</title>
<style>
  body { font: 14px/1.5 -apple-system, sans-serif; margin: 2rem; color: #1a1a2e; }
  h1 { font-size: 1.3rem; } h2 { font-size: 1.05rem; margin-top: 2rem; }
  table { border-collapse: collapse; width: 100%; }
  td, th { border-bottom: 1px solid #ddd; padding: 4px 8px; text-align: left; }
  input { padding: 4px 8px; width: 24rem; }
  pre { background: #f5f5fa; padding: 1rem; overflow-x: auto; }
  .sev-warning, .sev-critical { color: #b00020; font-weight: 600; }
</style>
</head>
<body>
<h1>Paiziq local dashboard</h1>
<p>Read-only view over the configured ingest backend (proxied server-side).</p>

<h2>Recent notifications</h2>
<table id="notifications"><tr><th>severity</th><th>title</th><th>request</th></tr></table>

<h2>Trace lookup</h2>
<input id="trace" placeholder="trace id"> <button onclick="loadTrace()">replay</button>
<pre id="spans">—</pre>

<script>
async function refresh() {
  const res = await fetch('/api/v1/notifications');
  const body = await res.json();
  const rows = (body.notifications || []).map(n =>
    `<tr><td class="sev-${n.severity}">${n.severity}</td><td>${n.title}</td>` +
    `<td>${n.request_id || ''}</td></tr>`).join('');
  document.getElementById('notifications').innerHTML =
    '<tr><th>severity</th><th>title</th><th>request</th></tr>' + rows;
}
async function loadTrace() {
  const id = document.getElementById('trace').value.trim();
  if (!id) return;
  const res = await fetch('/api/v1/traces/' + encodeURIComponent(id));
  const body = await res.json();
  document.getElementById('spans').textContent = JSON.stringify(body.spans, null, 2);
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def write_bundle(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    page = directory / "index.html"
    page.write_text(_PAGE)
    return page


def _make_handler(transport: Any) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            if self.path.startswith("/api/"):
                self._proxy(self.path[len("/api"):])
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_PAGE.encode())

        def _proxy(self, path: str) -> None:
            """Read-only pass-through; the key stays server-side."""
            try:
                response = transport.request("GET", path)
                body, status = response.body, response.status
            except Exception as exc:  # backend down → 502, not a crash
                body, status = f'{{"error": "{exc}"}}'.encode(), 502
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:  # keep CLI output clean
            pass

    return DashboardHandler


def serve(port: int, transport_factory: Optional[TransportFactory] = None) -> ThreadingHTTPServer:
    """Start the dashboard server (returns it; caller decides blocking)."""
    config = load_config()
    factory = transport_factory or default_transport_factory
    transport = factory(config.require_endpoint(), config.require_api_key())
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(transport))
    return server


def cmd_dashboard_deploy(args: Any, factory: Optional[TransportFactory] = None) -> int:
    page = write_bundle(Path(args.dir))
    print(f"wrote {page}")
    print("serve it with: paiziq dashboard serve")
    return 0


def cmd_dashboard_serve(args: Any, factory: Optional[TransportFactory] = None) -> int:
    server = serve(args.port, transport_factory=factory)
    host, port = server.server_address[:2]
    host_str = host.decode() if isinstance(host, bytes) else str(host)
    print(f"dashboard on http://{host_str}:{port} (Ctrl-C to stop)")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()
    return 0
