# Python >=3.14移行・依存セキュリティ検証

## スコープと完了条件

標準CPython 3.14/3.15 × Windows/Linuxを必須とする。最低Python要件は>=3.14。
Python 3.13以下、free-threaded版、PyPy、macOS、実利用環境への配備は対象外。
HTTP API・設定・会話・添付のデータ形式を維持する。大規模な画像/PDF処理分離は後続課題。

完了ゲートは以下。テストの成功だけでは全件対応完了としない。

1. A: 全openアラートの対応表、4環境でlock不変のwheelインストール・依存整合性・native import。
2. B: 4環境で全回帰・Ruff・mypy合格。画像・PDF・TLS・MCP・日本語保存の互換性確認。
3. C: wheel/sdistの資産とPython/依存メタデータ確認、通常pipインストールから全テスト、CLI起動・HTTP応答・正常終了。
4. D: mainのCIが合格し、Dependabot再解析後のopenが0件。基準45件はdismissではなく全件fixed。

ゲートDの再確認:

```powershell
uv run --extra dev python scripts/check_dependabot_resolution.py
```

このコマンドはghの読み取り専用APIで全状態を取得する。新規openも失敗条件になる。

## アラートと更新結果

実装開始時のgh再取得でも45件。初回の[取得JSON](dependabot-alerts-open-2026-10-25.json)に対しIDの追加・削除は0件。
個別ID・GHSA・脆弱範囲は[全45件一覧](dependabot-response-plan-2026-10-25.md#全45件の一覧)に保存。

| 対応対象 | 件数 | 元lock | 更新lock | パッケージ定義の下限 |
| --- | --- | --- | --- | --- |
| Pillow: #49–57, #59–88, #92–93 | 41 | 12.3.0 | 13.0.0 | >=13.0.0,<14 |
| pypdf: #89–91 | 3 | 6.21.1 | 6.21.2 | >=6.21.2,<7 |
| certifi: #58 | 1 | 2026.6.17 | 2026.10.23 | >=2026.10.19 |

修正版で脆弱範囲を外す。GitHub上のfixed確定はゲートDで記録する。
certifiは指定更新時の安定版2026.10.23を採用。下限2026.10.19は維持。

## 追加依存更新の根拠

Python 3.15正式版で`uv sync --locked --extra dev --no-build --no-install-project`を実行し、旧lockのlxmlに対応wheelがなく失敗することを確認。
追加調査は3.15で`uv pip install --dry-run --only-binary :all: --resolution lowest-direct`を使用。
3.15の安定版を使用し、ベータ版での検証を正式版の合格には代用しない。

| 依存 | 旧→新 | 必要性・利用経路 |
| --- | --- | --- |
| lxml | 6.1.1→6.1.2 | cp315 wheel。ddgsのHTML処理 |
| cffi | 2.0.0→2.1.0 | cp315 wheel。cryptographyの依存 |
| MarkupSafe | 3.0.3→3.0.4 | cp315 wheel。Jinja2の依存 |
| librt | 0.11.0→0.14.0 | cp315 wheel。mypyの依存 |
| Pydantic / core | 2.13.4/2.46.4→2.14.0/2.50.0 | 3.15 wheelを提供するcoreと組になる最小のPydantic。MCP・Ollama・設定schema |
| typing-inspection | 0.4.2→0.4.4 | 更新後Pydanticの必要条件 |

上記以外を一括アップグレードしない。旧Python向けexceptiongroup/tomliと、rpds-py・typing-extensionsの不要な条件分岐はPython要件変更に伴って整理。
通常pipでのインストール検証はlockを使用しないため、最新の許容依存が選ばれる。lockの更新対象拡大とは区別する。

## アプリ互換性分析

| 対象 | 分析・検証 | 分類と処置 |
| --- | --- | --- |
| 画像 | PNG/JPEG/GIF/BMP/WebP/TIFF、透過・パレット・16bit入力を実際に縮小・再保存し、サイズ・形式・modeを確認。出力が元バイト列に戻っていないことも確認 | テスト合格時は互換性維持。複数フレームは従来どおり先頭フレームのみ |
| PillowのIモードPNG変更 | `resize_image`はRGB変換後に保存。I;16の入力でPNGへの再保存を検証 | アプリの一括書換えは不要 |
| 不正画像・上限 | 壊れた入力・ピクセル上限時の既存fallbackを明示的に検証 | 挙動維持。例外捕捉をネイティブ脆弱性対策と扱わずPillowを更新 |
| PDF | 既存の正常PDF・添付プレビュー・ページ内外継続・旧トークンのテストを維持。欠損PDFと異常xrefを子プロセス・10秒上限で追加検証 | 高負荷/コード実行PoCは実行せず、有限時間の処理終了を確認 |
| MCP/asyncio・型情報 | stdioで日本語echo、HTTPの列挙/呼出し/DELETE/クライアント解放、キャンセル時解放。FastMCPでschema生成を実行 | `asyncio.run()`を維持。Pydantic更新後も実利用経路で検証 |
| TLS | ローカル生成証明書で信頼CA指定時の成功・既定ストアでの拒否 | certifi更新後も証明書検証を維持。外部サイトは自動テスト条件にしない |
| 日本語・永続化 | 更新前形式の会話を再読込、CP932添付をAPI経由で保存・再読込、設定・MCP JSON往復 | データ移行不要。UTF-8明示とCP932 fallbackを維持 |
| CLI/起動 | 配布メタデータのentry pointから実Uvicornを起動し、HTTP応答と正常停止を確認 | テスト専用wrapperでstdin終了を追加。製品CLIの仕様変更なし |
| UTC表記 | Ruffのpy314設定で既存`timezone.utc`がUP017対象 | `datetime.UTC`へ置換。保存するISO日時形式は変更なし |
| 認証/検索/ストリーム/音声 | 既存スイートを更新前後で実行 | 外部サービスは既存mockで置換。API・既存の権限チェックを維持 |

`tests/test_runtime_upgrade.py`と`tests/test_dependency_security.py`に追加の挙動検証を配置。
入力デコーダの許可リスト、画像/PDF処理のプロセス分離は別対応とする。

## 実測記録

- 更新前: Windows / Python 3.12.9、230 passed。既存のStarlette/AnyIO警告1件。
- 同一入力の比較: 追加した画像・PDF・TLS・日本語保存・CLI・MCPの24件を旧Python 3.12/旧依存でも実行し、24 passed。正常入力の回帰は再現していない。
- 更新後のローカル検証: Windows / Python 3.14.3・3.15.0（正式版）。結果はPR/CIの記録と照合する。
- 3.15では追加後245 passed、Ruff合格、mypy 30ファイル合格。通常pipの独立wheel導入でも245 passed。
- stdioキャンセル時の子プロセス終了検証を追加後、3.14の独立wheel導入で246 passed。最終CIは246件を検証する。
- Linuxを含む4環境の最終結果とmain後のアラート状態は、CIとゲートD確認後に追記する。

## 利用環境の移行

日英READMEに、データバックアップ、新規仮想環境、Python 3.14/3.15、既存データへの接続、MCPの絶対パス/allowlist更新、起動後確認を記載。
今回の作業で既存`.venv`や利用中の`data`は移行しない。旧リリースに戻すと脆弱な依存も戻るため、更新後構成での修正を優先する。
