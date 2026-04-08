"""
Lightweight HTTP frontend for the LAN file-sharing TCP server.
Uses only Python standard libraries. No external dependencies.

Usage:
    1. Start the TCP server:    python3 server.py
    2. Start the web server:    python3 web_server.py
    3. Open browser:            http://<LAN_IP>:8080
"""

import argparse
import json
import socket
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TCP_HOST = "127.0.0.1"
TCP_PORT = 5001
WEB_PORT = 8080
CHUNK_SIZE = 65536
HEADER_MAX_BYTES = 4096
SOCKET_TIMEOUT = 30


# ── TCP client helpers (reuse the same protocol as client.py) ──


def tcp_send_json(sock: socket.socket, payload: dict) -> None:
    sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))


def tcp_recv_line(sock: socket.socket) -> str:
    data = bytearray()
    while len(data) < HEADER_MAX_BYTES:
        chunk = sock.recv(1)
        if not chunk:
            break
        data += chunk
        if chunk == b"\n":
            break
    return data.decode("utf-8").strip()


def tcp_recv_json(sock: socket.socket) -> dict:
    payload = json.loads(tcp_recv_line(sock))
    if payload.get("status") != "OK":
        raise ValueError(payload.get("message", "Server error"))
    return payload


def tcp_request(header: dict) -> dict:
    with socket.create_connection((TCP_HOST, TCP_PORT), timeout=SOCKET_TIMEOUT) as s:
        tcp_send_json(s, header)
        return tcp_recv_json(s)


# ── HTML page (embedded) ──

HTML_FILE = Path(__file__).resolve().parent / "index.html"


# ── HTTP Handler ──


class Handler(BaseHTTPRequestHandler):

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._html(HTML_FILE.read_text("utf-8"))

        elif path == "/api/list":
            self._proxy_json({"command": "LIST"})

        elif path == "/api/search":
            qs = urllib.parse.parse_qs(parsed.query)
            keyword = qs.get("keyword", [""])[0]
            if not keyword:
                self._json_error("keyword required")
            else:
                self._proxy_json({"command": "SEARCH", "keyword": keyword})

        elif path.startswith("/api/download/"):
            filename = urllib.parse.unquote(path[len("/api/download/"):])
            self._proxy_download(filename)

        else:
            self._json_error("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path.startswith("/api/delete/"):
            filename = urllib.parse.unquote(self.path[len("/api/delete/"):])
            self._proxy_json({"command": "DELETE", "filename": filename})
            return

        if self.path != "/api/upload":
            self._json_error("Not found", HTTPStatus.NOT_FOUND)
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._json_error("Expected multipart/form-data")
            return

        # Parse boundary
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):].strip('"')
        if not boundary:
            self._json_error("Missing boundary")
            return

        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        boundary_bytes = ("--" + boundary).encode()
        parts = body.split(boundary_bytes)

        filename = None
        file_data = None
        for part in parts:
            if b"Content-Disposition" not in part:
                continue
            header_end = part.find(b"\r\n\r\n")
            if header_end < 0:
                continue
            header_section = part[:header_end].decode("utf-8", errors="replace")
            payload = part[header_end + 4:]
            if payload.endswith(b"\r\n"):
                payload = payload[:-2]

            if 'name="file"' in header_section:
                file_data = payload
                for line in header_section.split("\r\n"):
                    if "filename=" in line:
                        fn_start = line.index('filename="') + 10
                        fn_end = line.index('"', fn_start)
                        filename = line[fn_start:fn_end]

        if not filename or file_data is None:
            self._json_error("No file received")
            return

        # Send to TCP server
        try:
            with socket.create_connection((TCP_HOST, TCP_PORT), timeout=SOCKET_TIMEOUT) as s:
                tcp_send_json(s, {"command": "UPLOAD", "filename": filename, "size": len(file_data)})
                s.sendall(file_data)
                resp = tcp_recv_json(s)
            self._json_ok(resp)
        except Exception as e:
            self._json_error(str(e))

    # ── Response helpers ──

    def _html(self, content: str):
        data = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_ok(self, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_error(self, msg: str, status=HTTPStatus.BAD_REQUEST):
        data = json.dumps({"status": "ERROR", "message": msg}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _proxy_json(self, header: dict):
        try:
            resp = tcp_request(header)
            self._json_ok(resp)
        except Exception as e:
            self._json_error(str(e))

    def _proxy_download(self, filename: str):
        try:
            with socket.create_connection((TCP_HOST, TCP_PORT), timeout=SOCKET_TIMEOUT) as s:
                tcp_send_json(s, {"command": "DOWNLOAD", "filename": filename})
                resp = tcp_recv_json(s)
                size = resp["size"]

                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(size))
                self.end_headers()
                self.wfile.flush()

                received = 0
                while received < size:
                    chunk = s.recv(min(CHUNK_SIZE, size - received))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except BrokenPipeError:
            pass  # Client disconnected mid-download, nothing to do
        except Exception as e:
            try:
                self._json_error(str(e))
            except BrokenPipeError:
                pass

    def address_string(self):
        # Skip reverse DNS lookup — avoids multi-second delay on LAN
        return self.client_address[0]

    def log_message(self, format, *args):
        print(f"[WEB] {self.address_string()} - {format % args}")


# ── Main ──


def get_local_ip() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Web frontend for LAN file server")
    parser.add_argument("--port", type=int, default=WEB_PORT, help=f"HTTP port (default: {WEB_PORT})")
    parser.add_argument("--tcp-host", default=TCP_HOST, help=f"TCP server host (default: {TCP_HOST})")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT, help=f"TCP server port (default: {TCP_PORT})")
    args = parser.parse_args()

    TCP_HOST = args.tcp_host
    TCP_PORT = args.tcp_port

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler, bind_and_activate=False)
    server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.server_bind()
    server.server_activate()
    local_ip = get_local_ip()
    print(f"Web server running on http://0.0.0.0:{args.port}")
    print(f"Open in browser: http://{local_ip}:{args.port}")
    print(f"Proxying to TCP server at {TCP_HOST}:{TCP_PORT}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWeb server stopped.")
    finally:
        server.server_close()
