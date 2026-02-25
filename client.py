import argparse
import json
from pathlib import Path
import shlex
import socket


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001
CHUNK_SIZE = 4096
SOCKET_TIMEOUT_SECONDS = 30


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LAN file client (metadata step)")
    parser.add_argument(
        "--host",
        default=None,
        help=f"Server IP/hostname (prompted if omitted, default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Server TCP port (prompted if omitted, default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "file_path",
        nargs="?",
        help="Local file path to upload (you can drag a file into terminal)",
    )
    return parser.parse_args()


def resolve_file_path(raw_path: str | None) -> Path:
    if raw_path:
        typed = raw_path.strip()
    else:
        typed = input("Enter local file path: ").strip()

    if not typed:
        raise ValueError("Local file path is required.")

    # Accept pasted/dragged paths with quotes or shell escaping.
    try:
        parts = shlex.split(typed)
        if len(parts) == 1:
            typed = parts[0]
    except ValueError:
        pass

    return Path(typed).expanduser().resolve()


def validate_file(path: Path) -> None:
    if not path.exists():
        raise ValueError(f"File does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")


def resolve_server_target(
    raw_host: str | None, raw_port: int | None
) -> tuple[str, int]:
    host = raw_host
    if host is None:
        typed_host = input(
            f"Enter server IP address. Press Enter to use default: [{DEFAULT_HOST}]: "
        ).strip()
        host = typed_host or DEFAULT_HOST

    port = raw_port
    if port is None:
        typed_port = input(
            f"Enter server port. Press Enter to use default: [{DEFAULT_PORT}]: "
        ).strip()
        port = int(typed_port) if typed_port else DEFAULT_PORT

    if not (1 <= port <= 65535):
        raise ValueError("--port must be between 1 and 65535")
    return host, port


def send_upload_header(sock: socket.socket, filename: str, filesize: int) -> None:
    header = {"command": "UPLOAD", "filename": filename, "size": filesize}
    sock.sendall((json.dumps(header) + "\n").encode("utf-8"))


def send_file_contents(sock: socket.socket, file_path: Path) -> int:
    sent = 0
    with file_path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            sock.sendall(chunk)
            sent += len(chunk)
    return sent


def recv_server_response(sock: socket.socket) -> str:
    data = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in chunk:
            break

    if not data:
        raise ValueError("No confirmation response from server.")
    return data.decode("utf-8", errors="replace").strip()


def upload_file(
    host: str, port: int, file_path: Path, filename: str, filesize: int
) -> str:
    with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT_SECONDS) as sock:
        sock.settimeout(SOCKET_TIMEOUT_SECONDS)

        # Server sends a greeting line first; read and ignore if present.
        _ = recv_server_response(sock)

        send_upload_header(sock, filename, filesize)
        bytes_sent = send_file_contents(sock, file_path)
        if bytes_sent != filesize:
            raise ValueError(
                f"Sent size mismatch: expected {filesize}, sent {bytes_sent}"
            )

        return recv_server_response(sock)


def ensure_upload_success(server_response: str) -> None:
    if not server_response.startswith("UPLOAD OK"):
        raise ValueError(f"Upload unsuccessful. Server said: {server_response}")


def main() -> None:
    args = parse_args()
    host, port = resolve_server_target(args.host, args.port)

    file_path = resolve_file_path(args.file_path)
    validate_file(file_path)

    filename = file_path.name
    filesize = file_path.stat().st_size

    print("Input validated.")
    print(f"Server: {host}:{port}")
    print(f"Local file: {file_path}")
    print(f"Filename: {filename}")
    print(f"Filesize: {filesize} bytes")
    print("Uploading...")

    response = upload_file(host, port, file_path, filename, filesize)
    ensure_upload_success(response)
    print(f"Server response: {response}")


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        raise SystemExit(f"Error: {e}")
    except socket.timeout:
        raise SystemExit(f"Error: socket timed out after {SOCKET_TIMEOUT_SECONDS}s")
    except ConnectionRefusedError:
        raise SystemExit("Error: connection refused. Check server IP/port and server status.")
    except socket.gaierror as e:
        raise SystemExit(f"Error: cannot resolve server host: {e}")
    except OSError as e:
        raise SystemExit(f"Error: network failure: {e}")
    except KeyboardInterrupt:
        raise SystemExit("\nCancelled by user.")
    except EOFError:
        raise SystemExit("\nInput aborted.")
