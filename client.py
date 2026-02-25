import argparse
from pathlib import Path
import shlex


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001


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
            f"Enter server IP/hostname. Press Enter to use default: [{DEFAULT_HOST}]: "
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
    print("Ready for next step: sending header and file content.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        raise SystemExit(f"Error: {e}")
    except KeyboardInterrupt:
        raise SystemExit("\nCancelled by user.")
    except EOFError:
        raise SystemExit("\nInput aborted.")
