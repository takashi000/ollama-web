# ollama-web

Docker や仮想マシンを必要とせず、ホスト OS 上で直接起動して利用できる、軽量な個人向け Ollama Web UI です。

ブラウザから Ollama とのストリーミングチャット、Web 検索・URL 取得、画像・ファイル・PDF 添付、MCP Tool Calling を利用できます。大規模で複雑な AI プラットフォームを導入せず、個人利用に必要な機能を小さな構成で使うことを意図しています。

<img src="./docs/capture.gif" width="100%" alt="Screenshot of the chat" />

## 主な特徴

- Ollama モデルとのストリーミングチャット
- Web 検索・URL 取得
- 画像・ファイル・PDF 添付
- stdio / Streamable HTTP による MCP ツール連携
- Docker / VM 不要でホスト OS 上で直接動作
- 会話履歴と添付ファイルのローカル保存
- PIN 認証、CSRF・CSP・SSRF 対策
- 日本語・英語 UI

## Requirements

- Python 3.14以上（標準CPython。Windows/Linux × 3.14・3.15で検証）
- Ollama が起動していること
- `uv` 推奨

既定では Ollama API を `http://127.0.0.1:11434` として扱います。

## Installation

```bash
uv venv --python 3.14 .venv
uv pip install -e ".[dev]" -p .venv
```

本番依存だけを入れる場合:

```bash
uv venv .venv
uv pip install -e . -p .venv
```

### Python 3.13以前からの移行

アプリを停止し、`OLLAMA_WEB_DATA_DIR`のディレクトリ（既定は`data`）を
会話・添付・`settings.json`・`mcpServers.json`ごとバックアップしてください。
PIN・秘密鍵などの環境変数も引き継ぎます。以前の仮想環境は流用せず、新しく作成します。

```bash
uv venv --python 3.14 .venv-314
uv pip install -e . --python .venv-314
uv run --python .venv-314 --no-project ollama-web
```

Python 3.15でも同じ手順で移行できます。データ形式の変更はないため、
`OLLAMA_WEB_DATA_DIR`は既存データを指すようにしてください。
MCPが以前の仮想環境のPythonを使っている場合は、`command`の絶対パスと
`OLLAMA_WEB_MCP_STDIO_ALLOWLIST`を両方更新します。
再起動後にログイン、会話再読込、添付、ストリーミング、MCP呼出しを確認してください。
旧リリースへ戻すと脆弱な依存も戻るため、更新済み環境での修正を優先します。
詳細は[互換性・検証記録](docs/python314-security-validation.md)を参照してください。

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
| `OLLAMA_WEB_SEARCH_REGION` | `auto` | 検索地域。`auto` / `us-en` / `jp-jp`。auto はかなを含むクエリを日本、それ以外を米国として検索 |

検索は ddgs の `backend="auto"` を使用します。地域は内部で選び、LLM に公開する引数は
`query` と `max_results` のままです。判定では半角カナも正規化し、URL と `site:` / `filetype:`
のトークンを除外します。検索へ送る元クエリは変更しません。漢字だけのクエリは `us-en`
になるため、日本へ固定する場合は `jp-jp` を設定してください。UI 言語には連動せず、
設定変更の反映にはアプリの再起動が必要です。不正な設定値では起動時にエラーになります。

構文上無効な URL と既知の Bing 広告リンクを除外し、空・URL 形式のタイトルは空文字にして
URL と要約を保持します。除外で件数が減っても追加検索は行いません。provider の失敗は
ツールエラーとして伝えます。

### 多言語対応

UI 文言と LLM プロンプトは、それぞれ `src/ollama_web/i18n/messages/` と `src/ollama_web/prompts/` の JSON ファイルで管理されています。既定は日本語（`ja`）です。新しい言語を追加する場合は、同じディレクトリに `{lang}.json` を作成し、`OLLAMA_WEB_LANGUAGE` をその言語コードに設定してください。

### 一般設定

ブラウザの左ペイン下部にある「一般設定」から、表示言語、システムプロンプト、Ollama の生成Optionsを変更できます。設定は `OLLAMA_WEB_DATA_DIR/settings.json` に保存され、このデータディレクトを利用する全セッションで共有されます。

保存済みの表示言語は `OLLAMA_WEB_LANGUAGE` より優先されます。システムプロンプトは内蔵のTool用プロンプトの後に追加され、空欄の詳細Optionsと乱数モードの `seed` はOllamaへ送信されません。

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

MCP 設定 API の GET 応答では、`env` / `headers` 内の secret らしい値は `***` にマスクされます。Tool の実行結果やエラーも LLM へ渡す前に不信データとして隔離されます。

MCP Tool が画像または音声を返した場合、base64 はテキスト結果から分離し、サイズと MIME type を検証してセッションへ保存します。画像は vision 対応が明示されたモデルにだけ入力し、非対応モデルには多言語プロンプトで固定の通知を渡します。音声データはモデルの capability にかかわらず Ollama へ渡さず、チャット上の同一オリジン URL から `<audio controls>` で再生します。MCP の `annotations.audience` も反映されます。

標準の `web_search`、`scrape_url`、`search_and_fetch` はテキスト取得専用です。検索結果の画像 URL を自動取得せず、後二者は画像・音声・動画のレスポンスを拒否します。詳しい境界と理由は [マルチメディア処理の設計](docs/multimedia.md) を参照してください。

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
- MCP の stdio transport は allowlist 未設定では拒否されます。

## Development

```bash
# 単体・統合テスト
pytest -m "not ui"

# UIテスト（実ChromiumとローカルのOllama/MCP模擬サーバー）
playwright install chromium
pytest -m ui

# Lint
ruff check .

# 型チェック
mypy src/
```

このリポジトリでは `pyproject.toml` に pytest、ruff、mypy の設定をまとめています。

ツールの実行件数・ラウンド数・結果文字数に固定上限はありません。内蔵・MCP共通で、
入力全体の使用率を参照した「ツール取得の停止ライン」（初期値80%）を適用します。
一般設定 → ツール設定で変更でき、1回の取得量は自動調整します。
通常回答やツール停止後の最終回答は、この停止ラインでは拒否しません。
使用量は推定し、Ollamaの実測で補正します。
容量の決定方法と推定の制約は[ツール取得の停止ライン](docs/tools-schema.md#ツール取得の停止ラインと設定)を参照してください。
停止ボタンは既存のチャット接続を切断し、専用ワーカーと所有するstdioプロセスを終了します。
外部サーバーが受理済みの処理は継続する場合があります。
詳細は [ツール仕様](docs/tools-schema.md) を参照してください。
実Ollamaのテストも実行する場合は、`OLLAMA_WEB_LIVE_MODEL` に利用可能なモデル名を指定します。


## License

SPDX-License-Identifier: MIT

This project is licensed under the MIT License.  
See [LICENSE](LICENSE) for the full text.

Third-party libraries bundled with or depended upon by this project are listed in
[THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt).
