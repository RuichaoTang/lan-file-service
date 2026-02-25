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
        raise ValueError("No upload header received.")
    if b"\n" not in data:
        raise ValueError("Upload header too long or missing newline.")

    return data.decode("utf-8").strip()


def parse_upload_header(header_line: str) -> tuple[str, str, int]:
    # Expected one-line JSON:
    # {"command":"UPLOAD","filename":"test.txt","size":123}
    try:
        header = json.loads(header_line)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON header.") from exc

    if not isinstance(header, dict):
        raise ValueError("Header JSON must be an object.")

    command = header.get("command")
    filename = header.get("filename")
    file_size = header.get("size")

    if not isinstance(command, str) or not command:
        raise ValueError("Header field 'command' must be a non-empty string.")
    if not isinstance(filename, str) or not filename:
        raise ValueError("Header field 'filename' must be a non-empty string.")
    if not isinstance(file_size, int):
        raise ValueError("Header field 'size' must be an integer.")
    if file_size < 0:
        raise ValueError("Header field 'size' must be non-negative.")

    command = command.upper()

    return command, filename, file_size


def get_destination_path(filename: str) -> Path:
    # Reject directory paths to avoid path traversal and keep uploads in shared/
    if Path(filename).name != filename:
        raise ValueError("Filename must not contain path separators.")
    if filename in {".", ".."}:
        raise ValueError("Invalid filename.")

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
                client_socket.sendall(b"Connected.\n")
                header_line = recv_line(client_socket)
                command, filename, file_size = parse_upload_header(header_line)
                if command != "UPLOAD":
                    raise ValueError(f"Unsupported command: {command}")

                destination = get_destination_path(filename)
                print(
                    f"Receiving upload from {client_addr}: "
                    f"filename={filename}, size={file_size}, dest={destination}"
                )
                bytes_written = receive_file_data(client_socket, destination, file_size)
                ok = f"UPLOAD OK: received {bytes_written} bytes into {destination.name}\n"
                print(ok.strip())
                client_socket.sendall(ok.encode("utf-8"))
            except socket.timeout:
                err = f"Upload error: timed out after {CLIENT_TIMEOUT_SECONDS}s\n"
                print(err.strip())
                try:
                    client_socket.sendall(err.encode("utf-8"))
                except OSError:
                    pass
            except ValueError as e:
                err = f"Upload error: {e}\n"
                print(err.strip())
                client_socket.sendall(err.encode("utf-8"))
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
