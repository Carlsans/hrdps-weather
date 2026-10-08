import http.server
import threading

import pytest

from hrdps_weather import net


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"                      # keep-alive

    def do_GET(self):
        if self.path.startswith("/missing"):
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        if self.path.startswith("/close"):             # the server drops the connection after answering
            body = b"bye"
            self.send_response(200); self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(body)
            self.close_connection = True
            return
        body = self.path.encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    class Srv(http.server.ThreadingHTTPServer):
        connections = 0

        def get_request(self):
            Srv.connections += 1
            return super().get_request()
    Srv.connections = 0
    srv = Srv(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_connection_is_reused(server):
    srv, base = server
    for i in range(6):
        assert net.get(f"{base}/a?{i}", "test") == f"/a?{i}".encode()
    assert srv.connections == 1                         # six requests, one TCP connection


def test_errors_return_none_and_reconnect_after_server_close(server):
    srv, base = server
    assert net.get(f"{base}/missing", "test") is None
    assert net.get(f"{base}/close", "test") == b"bye"
    assert net.get(f"{base}/after", "test") == b"/after"    # transparently reconnects


def test_unreachable_host_returns_none():
    assert net.get("http://127.0.0.1:9/x", "test", timeout=1, tries=1) is None


def test_each_thread_has_its_own_connection(server):
    srv, base = server
    out = []

    def work():
        out.extend(net.get(f"{base}/t{i}", "test") for i in range(3))
    ts = [threading.Thread(target=work) for _ in range(3)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(out) == 9 and all(out)
    assert srv.connections <= 4
