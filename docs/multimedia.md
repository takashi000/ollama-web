# マルチメディア処理の設計

## 境界と目的

画像処理の制限を CSP だけへ集約することはできません。CSP はブラウザが読み込める表示元を制限しますが、サーバー側ツールによる外部取得、LLM へ送るデータ、保存容量は制限しません。この実装では責務を次のように分けます。

| 境界 | 責務 |
|---|---|
| `safe_http` | SSRF、リダイレクト、取得サイズの共通制限 |
| 標準 Web ツール | テキストだけを返す契約。メディアを誤ってデコードしない |
| MCP 変換層 | text と image/audio artifact の分離、base64・MIME・件数・サイズの検証 |
| LLM 連携層 | model capability とメディア種別に基づく入力ポリシー |
| セッション配信と CSP | 認証済み同一オリジンから、許可した保存済みメディアだけを表示・再生 |

この切り分けにより、CSP にサーバー処理の責務を持たせず、同じ制限を各 URL 取得関数へ重複実装することも避けています。

## 標準 Web ツール

- `web_search` は title、URL、snippet の文字列だけを返します。結果 URL の画像やその他のバイナリを自動取得しません。
- `scrape_url` は読みやすい本文テキストの抽出専用です。取得後の `Content-Type` が `image/*`、`audio/*`、`video/*` なら拒否します。
- `search_and_fetch` は `pdf`、`txt`、`csv`、`md`、`json`、`html`、`htm`、`xml`、`py`、`js`、`ts` の明示的 allowlist に限定し、取得応答が画像・音声・動画なら拒否します。
- `safe_http` 自体は PDF 等のバイナリ取得にも使う共通基盤なので、メディア専用の拒否は追加しません。

ここでのメディア拒否はブラウザ攻撃対策ではなく、標準ツールの戻り値をテキストに統一するためのものです。

## LLM 入力ポリシー

| 入力元 | 種別 | Ollama へ渡す条件 | 非対応時の処理 |
|---|---|---|---|
| ユーザー添付 | 画像 | モデルが `vision` を明示 | 画像 bytes/base64 を渡さず、既存の多言語プロンプト定義から固定通知を渡す |
| ユーザー添付 | 音声 | 渡さない | 音声 bytes/base64 や誤デコードしたテキストを渡さず、多言語の固定通知だけを渡す |
| MCP Tool | 画像 | artifact が assistant 向けで、モデルが `vision` を明示 | 画像 bytes/base64 を渡さず、多言語の固定通知を渡す |
| MCP Tool | 音声 | 渡さない | capability にかかわらず音声 bytes/base64 を渡さず、多言語の固定通知だけを渡す |

音声は、Ollama と利用モデルの入力形式・capability 検出が十分に安定するまで明示的に UI 専用とします。音声を base64 テキストへ変換してプロンプトへ埋め込む処理は行いません。将来対応する場合は、capability 名、対応形式、サイズまたは時間上限、必要なら文字起こし経路を別機能として定義します。

固定通知は `prompts/ja.json` と `prompts/en.json` に置き、添付本文や既存のツール用プロンプトと同じ言語選択に従います。

## MCP artifact とチャット表示

MCP の `ImageContent`、`AudioContent`、対応 MIME の blob resource は text とは別の artifact に変換します。base64 は厳格に decode し、1回最大8件、各 artifact は既存の `max_upload_mb` 以下、MIME は小さな allowlist に限定します。base64 本文は SSE、セッション JSON、LLM のテキスト結果へ含めません。

`annotations.audience` がある場合は user/assistant の指定に従います。user 向け artifact だけをセッションへ保存して SSE では ID、名前、MIME、サイズのみを通知し、assistant 向け artifact だけを LLM 入力候補にします。

ブラウザは受け取ったメタデータから DOM API で `<img>` または `<audio controls preload="metadata">` を生成します。Markdown や MCP 由来 HTML は使いません。コンテンツ URL は次の制約を持ちます。

- 通常の認証 middleware を通る
- session ID と file ID を UUID 形式で検証し、そのセッション配下の保存パスだけを解決する
- 許可済み image/audio MIME だけを `inline` 配信する（拡張子では判定しない）
- `Range` リクエストに対応し、`Cache-Control: private, no-store` を付ける
- CSP は `media-src 'self'`、画像は既存の `img-src 'self' data: blob:` を使用する

## 意図的に導入しないもの

現在のローカル・個人利用モデルと既存の認証境界に対して、署名付き URL、独自 Range 実装、音声の再エンコード、ウイルススキャン、全形式の magic-byte parser は必須ではないため導入しません。MCP 接続自体の allowlist、取得・保存サイズ、認証、同一オリジン配信、MIME allowlist を現在の実用的な境界とします。
