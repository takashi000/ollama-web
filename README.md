# ollama-web

Ollama をブラウザから利用するためのローカル Web UI です。

Starlette ベースの軽量なサーバーで、チャット、セッション保存、ファイル添付、Web 検索/取得ツール、PDF テキスト抽出を提供します。既定ではローカルホストのみで待ち受け、PIN ログインで UI と API を保護します。

<img src="./docs/capture.gif" width="100%" alt="Screenshot of the chat" />

## Features

- Ollama モデルとのストリーミングチャット
- 会話セッションと添付ファイルのローカル保存
- 画像添付のリサイズと vision モデル向け送信
- Web 検索、URL スクレイピング、ファイル検索/取得、PDF テキスト抽出ツール
- MCP クライアント機能：stdio / Streamable HTTP の MCP サーバーからツールを利用可能
- PIN 認証、CSRF 保護、CSP、ローカル配布のフロントエンド依存ファイル
- SSRF 対策付きの外部 URL 取得

## Requirements

- Python 3.10+
- Ollama が起動していること
- `uv` 推奨

既定では Ollama API を `http://127.0.0.1:11434` として扱います。

## Installation

```bash
uv venv .venv
uv pip install -e ".[dev]" -p .venv
```

本番依存だけを入れる場合:

```bash
uv venv .venv
uv pip install -e . -p .venv
```

## Usage

ローカルPCだけで利用する場合:

```bash
ollama-web
```

`OLLAMA_WEB_PIN` を設定していない場合、起動時にランダムな PIN がコンソールに表示されます。ブラウザで `http://127.0.0.1:8000` を開き、その PIN でログインしてください。

LAN 内のスマホなど別端末からアクセスする場合:

```bash
OLLAMA_WEB_PIN=123456 OLLAMA_WEB_HOST=0.0.0.0 ollama-web
```

PowerShell では次のように指定します。

```powershell
$env:OLLAMA_WEB_PIN = "123456"
$env:OLLAMA_WEB_HOST = "0.0.0.0"
ollama-web
```

## Configuration

| Environment variable | Default | Description |
| --- | --- | --- |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | 接続先 Ollama API |
| `OLLAMA_WEB_MODEL` | `llama3.2` | 既定で選択するモデル |
| `OLLAMA_WEB_HOST` | `127.0.0.1` | Web UI の待受ホスト |
| `OLLAMA_WEB_PORT` | `8000` | Web UI の待受ポート |
| `OLLAMA_WEB_DATA_DIR` | `data` | セッションと添付ファイルの保存先 |
| `OLLAMA_WEB_MAX_UPLOAD_MB` | `20` | アップロードファイルの上限サイズ |
| `OLLAMA_WEB_PIN` | 起動時に自動生成 | ログイン用 PIN |
| `OLLAMA_WEB_SECRET_KEY` | 起動時に自動生成 | Cookie 署名鍵 |
| `OLLAMA_WEB_ALLOWED_ORIGINS` | 未設定 | CORS 許可オリジンのカンマ区切り |
| `OLLAMA_WEB_MCP_STDIO_ALLOWLIST` | 未設定 | stdio MCP で起動を許可する実行ファイル絶対パスのカンマ区切り |
| `OLLAMA_WEB_MCP_HTTPS_ALLOWLIST` | 未設定 | remote HTTPS MCP 接続を許可するホスト名のカンマ区切り |
| `OLLAMA_WEB_LANGUAGE` | `ja` | UI と LLM プロンプトの表示言語（`ja` または `en`） |

### 多言語対応

UI 文言と LLM プロンプトは、それぞれ `src/ollama_web/i18n/messages/` と `src/ollama_web/prompts/` の JSON ファイルで管理されています。既定は日本語（`ja`）です。新しい言語を追加する場合は、同じディレクトリに `{lang}.json` を作成し、`OLLAMA_WEB_LANGUAGE` をその言語コードに設定してください。

### MCP サーバー設定

`data/mcpServers.json` に接続先 MCP サーバーを記述します。ブラウザの左ペイン下部「MCP設定」からも編集・保存できます。

MCP は外部プログラムや外部サーバーの Tool を LLM へ公開する機能です。安全のため、登録できるサーバーと自動実行できる Tool には制限があります。

#### stdio MCP を使う場合

stdio MCP は既定では起動できません。`command` に指定する実行ファイルの絶対パスを、先に `OLLAMA_WEB_MCP_STDIO_ALLOWLIST` へ登録してください。

PowerShell の例：

```powershell
$env:OLLAMA_WEB_MCP_STDIO_ALLOWLIST = "C:\Users\you\src\python\ollama-web\.venv\Scripts\python.exe"
ollama-web
```

`mcpServers.json` の例：

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

`OLLAMA_WEB_MCP_STDIO_ALLOWLIST` で許可するのは、`args` に渡すスクリプトではなく `command` の実行ファイルです。たとえば `python.exe` で `scripts/my_server.py` を起動する場合も、allowlist へ登録するのは `python.exe` の絶対パスです。

`cwd` 未指定時は ollama-webの起動パスが作業ディレクトリとなります。

#### Streamable HTTP MCP を使う場合

ローカルの HTTP MCP は `http://127.0.0.1` または `http://localhost` のみ許可されます。

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

remote HTTPS MCP を使う場合は、接続先ホスト名を `OLLAMA_WEB_MCP_HTTPS_ALLOWLIST` に登録してください。

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

plain HTTP のリモートサーバー、private IP、metadata IP は拒否されます。

#### MCP Tool 名と secret の扱い

Server 名と Tool 名には英数字、`_`、`-` のみ使えます。`__` は内部の名前空間区切りとして使うため指定できません。

Tool 名に `delete`、`write`、`run`、`shell`、`secret` など危険操作を示す語が含まれる場合、その Tool は確認なしには実行されません。現時点では UI の承認フローはなく、該当 Tool は拒否されます。

MCP 設定 API の GET 応答では、`env` / `headers` 内の secret らしい値は `***` にマスクされます。Tool の実行結果やエラーも LLM へ渡す前に不信データとして隔離されます。

`scripts/` 以下には動作確認用の MCP サーバーが用意されています。

```bash
# stdio モードで起動
python scripts/calc_server.py

# Streamable HTTP モードで起動（既定ポート 9000）
python scripts/calc_server.py streamable-http

# ポートを明示して起動（ollama-web の 8000 と衝突しないように）
python scripts/calc_server.py streamable-http 9001
```

対応パラメータ：

| Transport | 必須 | 任意 |
| --- | --- | --- |
| stdio | `command` | `args`, `env`, `cwd`, `encoding`, `encoding_error_handler` |
| Streamable HTTP | `url` | `headers`, `timeout`, `sse_read_timeout`, `terminate_on_close` |


### Security Notes

- 既定の待受は `127.0.0.1` です。LAN に公開する場合だけ `OLLAMA_WEB_HOST=0.0.0.0` を明示してください。
- `OLLAMA_WEB_PIN` 未設定時の PIN は起動ごとに変わります。継続利用する場合は固定値を設定してください。
- CORS は既定で無効です。外部オリジンから API を呼ぶ必要がある場合のみ `OLLAMA_WEB_ALLOWED_ORIGINS` を設定してください。
- Web 取得系ツールは localhost/private IP/metadata IP へのアクセスを拒否します。
- MCP の stdio transport は allowlist 未設定では拒否されます。Tool 名や Server 名は安全な英数字/`_`/`-` のみ許可され、危険名の MCP Tool は確認なしには実行されません。

## Development

```bash
# テスト
pytest

# Lint
ruff check .

# 型チェック
mypy src/
```

このリポジトリでは `pyproject.toml` に pytest、ruff、mypy の設定をまとめています。


## License

SPDX-License-Identifier: MIT

This project is licensed under the MIT License.  
See [LICENSE](LICENSE) for the full text.

Third-party libraries bundled with or depended upon by this project are listed in
[THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt).
