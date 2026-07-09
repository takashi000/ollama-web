# ollama-web

A local web UI for using Ollama from a browser.

A lightweight Starlette-based server providing chat, session storage, file attachments, web search / fetch tools, and PDF text extraction. It listens on localhost only by default and protects the UI and API with PIN login.

<img src="./docs/capture.gif" width="100%" alt="Screenshot of the chat" />

## Features

- Streaming chat with Ollama models
- Local storage of conversation sessions and attachments
- Image attachment resizing and sending for vision models
- Web search, URL scraping, file search / fetch, and PDF text extraction tools
- MCP client support: use tools from stdio / Streamable HTTP MCP servers
- PIN authentication, CSRF protection, CSP, locally distributed frontend dependency files
- External URL fetching with SSRF protection

## Requirements

- Python 3.10+
- Ollama must be running
- `uv` recommended

By default, the Ollama API is accessed at `http://127.0.0.1:11434`.

## Installation

```bash
uv venv .venv
uv pip install -e ".[dev]" -p .venv
```

To install only production dependencies:

```bash
uv venv .venv
uv pip install -e . -p .venv
```

## Usage

For local PC use only:

```bash
ollama-web
```

If `OLLAMA_WEB_PIN` is not set, a random PIN will be printed to the console on startup. Open `http://127.0.0.1:8000` in your browser and log in with that PIN.

To access from another device on the LAN, such as a smartphone:

```bash
OLLAMA_WEB_PIN=123456 OLLAMA_WEB_HOST=0.0.0.0 ollama-web
```

In PowerShell, set the variables as follows:

```powershell
$env:OLLAMA_WEB_PIN = "123456"
$env:OLLAMA_WEB_HOST = "0.0.0.0"
ollama-web
```

## Configuration

| Environment variable | Default | Description |
| --- | --- | --- |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Target Ollama API |
| `OLLAMA_WEB_MODEL` | `llama3.2` | Default model to select |
| `OLLAMA_WEB_HOST` | `127.0.0.1` | Host the web UI listens on |
| `OLLAMA_WEB_PORT` | `8000` | Port the web UI listens on |
| `OLLAMA_WEB_DATA_DIR` | `data` | Directory for sessions and attachments |
| `OLLAMA_WEB_MAX_UPLOAD_MB` | `20` | Maximum upload file size in MB |
| `OLLAMA_WEB_PIN` | Generated automatically on startup | PIN for login |
| `OLLAMA_WEB_SECRET_KEY` | Generated automatically on startup | Cookie signing key |
| `OLLAMA_WEB_ALLOWED_ORIGINS` | Not set | Comma-separated list of allowed CORS origins |
| `OLLAMA_WEB_MCP_STDIO_ALLOWLIST` | Not set | Comma-separated absolute paths of executable files allowed for stdio MCP |
| `OLLAMA_WEB_MCP_HTTPS_ALLOWLIST` | Not set | Comma-separated hostnames allowed for remote HTTPS MCP connections |
| `OLLAMA_WEB_LANGUAGE` | `ja` | Display language for the UI and LLM prompts (`ja` or `en`) |

### Multilingual Support

UI text and LLM prompts are managed by JSON files in `src/ollama_web/i18n/messages/` and `src/ollama_web/prompts/`, respectively. The default is Japanese (`ja`). To add a new language, create `{lang}.json` in the same directories and set `OLLAMA_WEB_LANGUAGE` to that language code.

### General Settings

You can change the display language, system prompt, and Ollama generation options from "General Settings" at the bottom of the left pane in the browser. Settings are saved to `OLLAMA_WEB_DATA_DIR/settings.json` and shared across all sessions that use this data directory.

The saved display language takes precedence over `OLLAMA_WEB_LANGUAGE`. The system prompt is appended after the built-in tool prompt. Empty detailed options and the random seed in random mode are not sent to Ollama.

### MCP Server Configuration

MCP server connections are described in `data/mcpServers.json`. You can also edit and save them from "MCP Settings" at the bottom of the left pane in the browser.

MCP is a feature that exposes tools from external programs or external servers to the LLM. For security, there are restrictions on which servers can be registered and which tools can be executed automatically.

#### Using stdio MCP

stdio MCP cannot be started by default. Register the absolute path of the executable specified in `command` in `OLLAMA_WEB_MCP_STDIO_ALLOWLIST` first.

PowerShell example:

```powershell
$env:OLLAMA_WEB_MCP_STDIO_ALLOWLIST = "C:\Users\you\src\python\ollama-web\.venv\Scripts\python.exe"
ollama-web
```

Example `mcpServers.json`:

```json
{
  "mcpServers": {
    "calc": {
      "command": "C:\\Users\\you\\src\\python\\ollama-web\\.venv\\Scripts\\python.exe",
      "args": ["scripts/calc_server.py"]
    }
  }
}
```

What should be allowed in `OLLAMA_WEB_MCP_STDIO_ALLOWLIST` is the executable in `command`, not the script passed in `args`. For example, when launching `scripts/my_server.py` with `python.exe`, register the absolute path of `python.exe` in the allowlist.

When `cwd` is not specified, the working directory is the path from which ollama-web was launched.

#### Using Streamable HTTP MCP

Local HTTP MCP is allowed only for `http://127.0.0.1` or `http://localhost`.

```json
{
  "mcpServers": {
    "remote_calc": {
      "url": "http://127.0.0.1:9000/mcp",
      "headers": {
        "Authorization": "Bearer optional-token"
      },
      "timeout": 30
    }
  }
}
```

For remote HTTPS MCP, register the destination hostname in `OLLAMA_WEB_MCP_HTTPS_ALLOWLIST`.

```powershell
$env:OLLAMA_WEB_MCP_HTTPS_ALLOWLIST = "mcp.example.com"
ollama-web
```

```json
{
  "mcpServers": {
    "remote_calc": {
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer optional-token"
      }
    }
  }
}
```

Plain HTTP remote servers, private IPs, and metadata IPs are rejected.

#### MCP Tool Names and Handling of Secrets

Server names and tool names may only contain alphanumeric characters, `_`, and `-`. `__` is reserved as an internal namespace separator and cannot be used.

In GET responses from the MCP settings API, secret-like values in `env` / `headers` are masked as `***`. Tool execution results and errors are also quarantined as untrusted data before being passed to the LLM.

Example MCP servers for testing are provided under `scripts/`.

```bash
# Launch in stdio mode
python scripts/calc_server.py

# Launch in Streamable HTTP mode (default port 9000)
python scripts/calc_server.py streamable-http

# Launch with an explicit port (avoid colliding with ollama-web on 8000)
python scripts/calc_server.py streamable-http 9001
```

Supported parameters:

| Transport | Required | Optional |
| --- | --- | --- |
| stdio | `command` | `args`, `env`, `cwd`, `encoding`, `encoding_error_handler` |
| Streamable HTTP | `url` | `headers`, `timeout`, `sse_read_timeout`, `terminate_on_close` |


### Security Notes

- The default listen host is `127.0.0.1`. Set `OLLAMA_WEB_HOST=0.0.0.0` only when exposing to the LAN.
- When `OLLAMA_WEB_PIN` is not set, the PIN changes on each startup. Set a fixed value for continued use.
- CORS is disabled by default. Set `OLLAMA_WEB_ALLOWED_ORIGINS` only when the API needs to be called from an external origin.
- Web fetching tools reject access to localhost / private IPs / metadata IPs.
- MCP stdio transport is rejected when the allowlist is not configured.

## Development

```bash
# Tests
pytest

# Lint
ruff check .

# Type check
mypy src/
```

This repository keeps pytest, ruff, and mypy configurations in `pyproject.toml`.


## License

SPDX-License-Identifier: MIT

This project is licensed under the MIT License.  
See [LICENSE](LICENSE) for the full text.

Third-party libraries bundled with or depended upon by this project are listed in
[THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt).
