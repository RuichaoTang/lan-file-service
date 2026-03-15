import argparse
import json
from pathlib import Path
import socket


HOST = "0.0.0.0"  # Listen on all network interfaces
PORT = 5001
BACKLOG = 5  # Max queued connections before accept()
HEADER_MAX_BYTES = 4096
CHUNK_SIZE = 4096
SHARED_DIR = Path(__file__).resolve().parent / "shared"
CLIENT_TIMEOUT_SECONDS = 30


def get_local_ip() -> str:
    # Use a UDP socket trick to discover the primary LAN IP.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


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
        raise ValueError("No request header received.")
    if b"\n" not in data:
        raise ValueError("Request header too long or missing newline.")

    return data.decode("utf-8").strip()


def recv_json_header(sock: socket.socket) -> dict:
    try:
        payload = json.loads(recv_line(sock))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON header.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Header JSON must be an object.")
    return payload


def parse_command(header: dict) -> str:
    command = header.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Header field 'command' must be a non-empty string.")
    return command.strip().upper()


def validate_plain_filename(filename: str) -> str:
    if not isinstance(filename, str) or not filename:
        raise ValueError("Header field 'filename' must be a non-empty string.")
    if Path(filename).name != filename:
        raise ValueError("Filename must not contain path separators.")
    if filename in {".", ".."}:
        raise ValueError("Invalid filename.")
    return filename


def parse_upload_header(header: dict) -> tuple[str, int]:
    filename = validate_plain_filename(header.get("filename"))
    file_size = header.get("size")
    if not isinstance(file_size, int):
        raise ValueError("Header field 'size' must be an integer.")
    if file_size < 0:
        raise ValueError("Header field 'size' must be non-negative.")
    return filename, file_size


def get_destination_path(filename: str) -> Path:
    base_path = SHARED_DIR / filename
    if not base_path.exists():
        return base_path

    stem = base_path.stem
    suffix = base_path.suffix
    counter = 1
    while True:
        candidate = SHARED_DIR / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def get_existing_file_path(filename: str) -> Path:
    safe_name = validate_plain_filename(filename)
    path = SHARED_DIR / safe_name
    if not path.exists() or not path.is_file():
        raise ValueError(f"File not found: {safe_name}")
    return path


def receive_file_data(sock: socket.socket, destination: Path, expected_size: int) -> int:
    received = 0
    with destination.open("wb") as f:
        while received < expected_size:
            remaining = expected_size - received
            chunk = sock.recv(min(CHUNK_SIZE, remaining))
            if not chunk:
                raise ValueError(
                    f"Client disconnected early ({received}/{expected_size} bytes received)."
                )
            f.write(chunk)
            received += len(chunk)
    return received


def send_file_data(sock: socket.socket, source: Path) -> int:
    sent = 0
    with source.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            sock.sendall(chunk)
            sent += len(chunk)
    return sent


def safe_send_json(sock: socket.socket, payload: dict) -> None:
    try:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    except OSError:
        # Client may already be gone; ignore send failures during cleanup/error paths.
        pass


def cleanup_partial_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
    except OSError:
        # Keep server running even if cleanup fails.
        pass


def list_files() -> list[dict]:
    files = []
    for entry in sorted(SHARED_DIR.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_file():
            files.append({"name": entry.name, "size": entry.stat().st_size})
    return files


def search_files(keyword: str) -> list[dict]:
    if not isinstance(keyword, str):
        raise ValueError("Header field 'keyword' must be a string.")

    needle = keyword.strip().lower()
    if not needle:
        raise ValueError("Header field 'keyword' must be a non-empty string.")

    return [
        info for info in list_files() if needle in info["name"].lower()
    ]


def handle_upload(client_socket: socket.socket, header: dict, client_addr: tuple) -> None:
    destination: Path | None = None
    filename, file_size = parse_upload_header(header)
    destination = get_destination_path(filename)

    print(
        f"Receiving upload from {client_addr}: "
        f"filename={filename}, size={file_size}, dest={destination}"
    )

    try:
        bytes_written = receive_file_data(client_socket, destination, file_size)
    except Exception:
        cleanup_partial_file(destination)
        raise

    response = {
        "status": "OK",
        "command": "UPLOAD",
        "filename": filename,
        "stored_as": destination.name,
        "size": bytes_written,
    }
    print(f"UPLOAD OK: received {bytes_written} bytes into {destination.name}")
    safe_send_json(client_socket, response)


def handle_list(client_socket: socket.socket) -> None:
    safe_send_json(
        client_socket,
        {"status": "OK", "command": "LIST", "files": list_files()},
    )


def handle_search(client_socket: socket.socket, header: dict) -> None:
    keyword = header.get("keyword")
    safe_send_json(
        client_socket,
        {
            "status": "OK",
            "command": "SEARCH",
            "keyword": keyword,
            "files": search_files(keyword),
        },
    )


def handle_download(client_socket: socket.socket, header: dict) -> None:
    filename = validate_plain_filename(header.get("filename"))
    source = get_existing_file_path(filename)
    size = source.stat().st_size

    safe_send_json(
        client_socket,
        {
            "status": "OK",
            "command": "DOWNLOAD",
            "filename": source.name,
            "size": size,
        },
    )
    sent = send_file_data(client_socket, source)
    print(f"DOWNLOAD OK: sent {sent} bytes for {source.name}")


def handle_client(client_socket: socket.socket, client_addr: tuple) -> None:
    header = recv_json_header(client_socket)
    command = parse_command(header)

    if command == "UPLOAD":
        handle_upload(client_socket, header, client_addr)
    elif command == "LIST":
        handle_list(client_socket)
    elif command == "SEARCH":
        handle_search(client_socket, header)
    elif command == "DOWNLOAD":
        handle_download(client_socket, header)
    else:
        raise ValueError(f"Unsupported command: {command}")


def start_server(port: int = PORT) -> None:
    # 1) Create a TCP socket (AF_INET + SOCK_STREAM)
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Allow quick restart without "Address already in use" issues
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        # 2) Bind socket to host + port
        server_socket.bind((HOST, port))

        # 3) Start listening; backlog controls pending connection queue size
        server_socket.listen(BACKLOG)
        SHARED_DIR.mkdir(parents=True, exist_ok=True)
        local_ip = get_local_ip()
        print(f"Server listening on {HOST}:{port} (backlog={BACKLOG})")
        print(f"Clients in LAN can connect to: {local_ip}:{port}")
        print(f"Shared directory: {SHARED_DIR}")

        while True:
            # 4) Accept incoming connection (blocks until a client connects)
            client_socket, client_addr = server_socket.accept()
            client_socket.settimeout(CLIENT_TIMEOUT_SECONDS)
            print(f"Accepted connection from {client_addr}")

            try:
                handle_client(client_socket, client_addr)
            except socket.timeout:
                err = {
                    "status": "ERROR",
                    "message": f"Request timed out after {CLIENT_TIMEOUT_SECONDS}s",
                }
                print(err["message"])
                safe_send_json(client_socket, err)
            except ValueError as e:
                err = {"status": "ERROR", "message": str(e)}
                print(f"Request error: {e}")
                safe_send_json(client_socket, err)
            except OSError as e:
                err = {"status": "ERROR", "message": f"socket I/O failure: {e}"}
                print(f"Request error: {err['message']}")
                safe_send_json(client_socket, err)
            finally:
                client_socket.close()

    except KeyboardInterrupt:
        print("\nServer stopped by user.")
    except Exception as e:
        print("Unexpected error:", e)
    finally:
        server_socket.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simple LAN TCP file server")
    parser.add_argument(
        "--port",
        type=int,
        default=PORT,
        help=f"TCP port to listen on (default: {PORT})",
    )
    args = parser.parse_args()
    if not (1 <= args.port <= 65535):
        parser.error("--port must be between 1 and 65535")
    start_server(port=args.port)
