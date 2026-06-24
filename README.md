# ollama-web

Ollama をブラウザから利用するためのローカル Web UI です。

Starlette ベースの軽量なサーバーで、チャット、セッション保存、ファイル添付、Web 検索/取得ツール、PDF テキスト抽出を提供します。既定ではローカルホストのみで待ち受け、PIN ログインで UI と API を保護します。

## Features

- Ollama モデルとのストリーミングチャット
- 会話セッションと添付ファイルのローカル保存
- 画像添付のリサイズと vision モデル向け送信
- Web 検索、URL スクレイピング、ファイル検索/取得、PDF テキスト抽出ツール
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

`requirements.txt` から本番依存だけを入れる場合:

```bash
uv venv .venv
uv pip install -r requirements.txt -p .venv
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

### Security Notes

- 既定の待受は `127.0.0.1` です。LAN に公開する場合だけ `OLLAMA_WEB_HOST=0.0.0.0` を明示してください。
- `OLLAMA_WEB_PIN` 未設定時の PIN は起動ごとに変わります。継続利用する場合は固定値を設定してください。
- CORS は既定で無効です。外部オリジンから API を呼ぶ必要がある場合のみ `OLLAMA_WEB_ALLOWED_ORIGINS` を設定してください。
- Web 取得系ツールは localhost/private IP/metadata IP へのアクセスを拒否します。

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

MIT
