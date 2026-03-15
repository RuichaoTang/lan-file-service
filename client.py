import argparse
import json
from pathlib import Path
import shlex
import socket


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001
CHUNK_SIZE = 4096
HEADER_MAX_BYTES = 4096
SOCKET_TIMEOUT_SECONDS = 30


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LAN file client")
    parser.add_argument(
        "--host",
        default=None,
        help=f"Server IP/hostname (legacy prompt mode asks if omitted; default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Server TCP port (legacy prompt mode asks if omitted; default: {DEFAULT_PORT})",
    )

    subparsers = parser.add_subparsers(dest="command", required=False)

    upload_parser = subparsers.add_parser("upload", help="Upload a local file")
    upload_parser.add_argument("file_path", help="Local file path to upload")

    subparsers.add_parser("list", help="List all files on server")

    search_parser = subparsers.add_parser("search", help="Search server files by keyword")
    search_parser.add_argument("keyword", help="Keyword to match in filenames")

    download_parser = subparsers.add_parser("download", help="Download a server file")
    download_parser.add_argument("filename", help="Filename stored on server")
    download_parser.add_argument(
        "--output",
        default=None,
        help="Local output path (default: ./downloads/<filename>)",
    )

    return parser.parse_args()


def resolve_file_path(raw_path: str) -> Path:
    typed = raw_path.strip()
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


def validate_port(port: int) -> None:
    if not (1 <= port <= 65535):
        raise ValueError("--port must be between 1 and 65535")


def resolve_server_target(
    raw_host: str | None, raw_port: int | None, interactive: bool
) -> tuple[str, int]:
    if raw_host is not None:
        host = raw_host
    elif interactive:
        typed_host = input(
            f"Enter server IP address. Press Enter to use default [{DEFAULT_HOST}]: "
        ).strip()
        host = typed_host or DEFAULT_HOST
    else:
        host = DEFAULT_HOST

    if raw_port is not None:
        port = raw_port
    elif interactive:
        typed_port = input(
            f"Enter server port. Press Enter to use default [{DEFAULT_PORT}]: "
        ).strip()
        port = int(typed_port) if typed_port else DEFAULT_PORT
    else:
        port = DEFAULT_PORT

    validate_port(port)
    return host, port


def prompt_command() -> str:
    typed = input("Choose command [upload/list/search/download]: ").strip().lower()
    if typed not in {"upload", "list", "search", "download"}:
        raise ValueError("Command must be one of: upload, list, search, download.")
    return typed


def send_json_header(sock: socket.socket, payload: dict) -> None:
    sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))


def recv_line(sock: socket.socket, max_bytes: int = HEADER_MAX_BYTES) -> str:
    data = bytearray()
    while len(data) < max_bytes:
        chunk = sock.recv(1)
        if not chunk:
            break
        data += chunk
        if chunk == b"\n":
            break

    if not data:
        raise ValueError("No response header from server.")
    if b"\n" not in data:
        raise ValueError("Response header too long or missing newline.")

    return data.decode("utf-8").strip()


def recv_json_header(sock: socket.socket) -> dict:
    try:
        payload = json.loads(recv_line(sock))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON response from server.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Response JSON must be an object.")

    if payload.get("status") != "OK":
        raise ValueError(payload.get("message", "Server returned an error."))

    return payload


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


def recv_file_contents(sock: socket.socket, output_path: Path, expected_size: int) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    received = 0
    with output_path.open("wb") as f:
        while received < expected_size:
            remaining = expected_size - received
            chunk = sock.recv(min(CHUNK_SIZE, remaining))
            if not chunk:
                raise ValueError(
                    f"Server disconnected early ({received}/{expected_size} bytes received)."
                )
            f.write(chunk)
            received += len(chunk)
    return received


def send_request(host: str, port: int, header: dict) -> dict:
    with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT_SECONDS) as sock:
        sock.settimeout(SOCKET_TIMEOUT_SECONDS)
        send_json_header(sock, header)
        return recv_json_header(sock)


def upload_file(host: str, port: int, file_path: Path) -> dict:
    filename = file_path.name
    filesize = file_path.stat().st_size

    with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT_SECONDS) as sock:
        sock.settimeout(SOCKET_TIMEOUT_SECONDS)
        send_json_header(
            sock,
            {"command": "UPLOAD", "filename": filename, "size": filesize},
        )
        bytes_sent = send_file_contents(sock, file_path)
        if bytes_sent != filesize:
            raise ValueError(
                f"Sent size mismatch: expected {filesize}, sent {bytes_sent}"
            )
        return recv_json_header(sock)


def download_file(host: str, port: int, filename: str, output_path: Path) -> tuple[dict, int]:
    with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT_SECONDS) as sock:
        sock.settimeout(SOCKET_TIMEOUT_SECONDS)
        send_json_header(sock, {"command": "DOWNLOAD", "filename": filename})
        header = recv_json_header(sock)

        size = header.get("size")
        if not isinstance(size, int) or size < 0:
            raise ValueError("Invalid DOWNLOAD response: missing valid 'size'.")

        received = recv_file_contents(sock, output_path, size)
        return header, received


def print_files(title: str, files: list[dict]) -> None:
    print(title)
    if not files:
        print("(empty)")
        return

    for item in files:
        name = item.get("name", "<unknown>")
        size = item.get("size", "?")
        print(f"- {name} ({size} bytes)")


def run_command(args: argparse.Namespace, command: str, host: str, port: int) -> None:
    if command == "upload":
        raw_file_path = getattr(args, "file_path", None)
        if raw_file_path is None:
            raw_file_path = input("Enter local file path (you can drag file here): ").strip()
        file_path = resolve_file_path(raw_file_path)
        validate_file(file_path)
        response = upload_file(host, port, file_path)
        print(
            "UPLOAD OK:",
            f"{response['filename']} -> {response['stored_as']} ({response['size']} bytes)",
        )
        return

    if command == "list":
        response = send_request(host, port, {"command": "LIST"})
        files = response.get("files", [])
        if not isinstance(files, list):
            raise ValueError("Invalid LIST response: 'files' must be a list.")
        print_files("Server files:", files)
        return

    if command == "search":
        keyword = getattr(args, "keyword", None)
        if keyword is None:
            keyword = input("Enter keyword to search: ").strip()
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("Keyword must be non-empty.")
        response = send_request(host, port, {"command": "SEARCH", "keyword": keyword})
        files = response.get("files", [])
        if not isinstance(files, list):
            raise ValueError("Invalid SEARCH response: 'files' must be a list.")
        print_files(f"Search results for '{keyword}':", files)
        return

    if command == "download":
        filename = getattr(args, "filename", None)
        if filename is None:
            filename = input("Enter filename to download: ").strip()
        filename = filename.strip()
        if not filename:
            raise ValueError("Filename must be non-empty.")

        output_value = getattr(args, "output", None)
        if output_value is None:
            typed_output = input(
                "Enter local output path (press Enter to use ./downloads/<filename>): "
            ).strip()
            output_value = typed_output or None

        output_path = (
            Path(output_value).expanduser().resolve()
            if output_value
            else (Path.cwd() / "downloads" / filename)
        )
        _, received = download_file(host, port, filename, output_path)
        print(f"DOWNLOAD OK: {filename} -> {output_path} ({received} bytes)")
        return

    raise ValueError(f"Unsupported command: {command}")


def main() -> None:
    args = parse_args()
    interactive_mode = args.command is None
    host, port = resolve_server_target(args.host, args.port, interactive=interactive_mode)
    command = args.command if args.command else prompt_command()
    run_command(args, command, host, port)


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
