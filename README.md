# Lan File Service

A lightweight TCP-based file sharing system for local networks.

## Iteration 2 protocol (JSON line based)

Each request starts with a single JSON header line (`\n` terminated).

### Commands

- `UPLOAD`
  - Request: `{"command":"UPLOAD","filename":"example.txt","size":123}`
  - Then client streams exactly `size` raw bytes.
- `LIST`
  - Request: `{"command":"LIST"}`
- `SEARCH`
  - Request: `{"command":"SEARCH","keyword":"report"}`
- `DOWNLOAD`
  - Request: `{"command":"DOWNLOAD","filename":"example.txt"}`

### Responses

- Success responses are JSON lines with `{"status":"OK", ...}`.
- Error responses are JSON lines with `{"status":"ERROR","message":"..."}`.
- For `DOWNLOAD`, server sends a success JSON header first (including `size`), then streams raw file bytes.

## Run

Start server:

```bash
python3 server.py --port 5001
```

Client examples:

```bash
python3 client.py                       # step-by-step prompts
python3 client.py --host 127.0.0.1 --port 5001 list
python3 client.py --host 127.0.0.1 --port 5001 search pdf
python3 client.py --host 127.0.0.1 --port 5001 upload /path/to/file.txt
python3 client.py --host 127.0.0.1 --port 5001 download file.txt --output ./file.txt
```
