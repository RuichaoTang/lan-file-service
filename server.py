import argparse
import socket


HOST = "0.0.0.0"  # Listen on all network interfaces
PORT = 5001
BACKLOG = 5  # Max queued connections before accept()


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
        local_ip = get_local_ip()
        print(f"Server listening on {HOST}:{port} (backlog={BACKLOG})")
        print(f"Clients in LAN can connect to: {local_ip}:{port}")

        while True:
            # 4) Accept incoming connection (blocks until a client connects)
            client_socket, client_addr = server_socket.accept()
            print(f"Accepted connection from {client_addr}")
            client_socket.sendall(b"You are connected to the LAN file server!\n")

            # For now, just close immediately (we will handle data next step)
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
