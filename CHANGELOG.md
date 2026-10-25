# Changelog

このプロジェクトの注目すべき変更は、このファイルに記録します。

形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) を参考にし、
バージョン番号は [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### Changed

- Pythonの最低要件を3.14へ引き上げ、Windows/Linux × CPython 3.14・3.15をCI対象にしました。3.13以前の仮想環境は再作成が必要です。
- Python 3.15用wheelのためcffi・librt・lxml・MarkupSafe・Pydantic/coreと必要なtyping-inspectionを更新しました。保存データ・HTTP APIの形式は変更していません。
- ddgs の最低バージョンとロックファイルを 9.16.0 へ更新しました。検索バックエンドは引き続き auto を使用します。
- Web検索の地域をクエリから内部選択し、`OLLAMA_WEB_SEARCH_REGION` で auto・米国・日本を切り替えられるようにしました。LLM向け引数は変更していません。

### Fixed

- Web検索の空・URL形式タイトルを欠損として扱い、無効URLとYahoo包装解除後の既知Bing広告リンクを除外するようにしました。検索失敗は引き続きツールエラーとして伝えます。

### Security

- Pillowを13.0.0、pypdfを6.21.2、certifiを2026.10.23へ更新しました。セキュリティ下限を通常pipインストールにも適用し、画像・PDF・TLS・MCP・CLIの回帰と配布物検証を追加しました。

## [0.5.4] - 2026-09-14

### Security

- Pillow を 12.3.0、pypdf を 6.16.1、h2 を 4.4.1、cryptography を 50.0.0 へ更新し、Dependabot で報告された画像・PDF・HTTP/2・暗号処理に関する脆弱性 21 件を解消しました。h2 の依存条件に合わせて hpack も 4.2.0 へ更新しました。

## [0.5.3] - 2026-09-13

### Added

- MCP の画像・音声結果をセッションへ保存し、認証済みの同一オリジン URL からチャット上で表示・再生できるようにしました。

### Changed

- 画像は vision 対応モデルだけへ渡し、非対応時は多言語の固定通知へ置き換えるようにしました。MCP 音声データはモデル capability にかかわらず Ollama へ渡しません。
- `web_search`、`scrape_url`、`search_and_fetch` をテキスト専用の契約に統一し、後二者は画像・音声・動画レスポンスを拒否するようにしました。

### Fixed

- スマートフォンやタッチ端末でセッション削除ボタンを押せない問題を修正し、hoverに依存しない表示、単一のイベント委譲、アプリ内確認ダイアログへ変更しました。

### Security

- MCP の base64 メディアをテキスト結果から分離し、MIME type・件数・サイズを制限しました。メディア配信は認証、セッションスコープ、MIME allowlist、`media-src 'self'` を適用します。

## [0.5.2] - 2026-08-26

### Changed

- PDFページングをページ番号とページ内文字位置の損失なしカーソルへ変更し、ローカル添付PDFもセッション限定URIで継続取得できるようにしました。

### Fixed

- ツール内部の8,000文字ページがLLM層で6,000文字へ再切断され、継続カーソルとの間に本文欠落が生じる問題を修正しました。
- 25ページを超えるPDFの後半が`search_and_fetch`で取得されない問題と、継続ごとに同じ取得ファイルが重複保存される問題を修正しました。
- 長いPDFを会話をまたいで取得する際、最新の`next_token`だけでソースとカーソルを復元できるようにし、古いトークンや誤った添付IDによって以前のページへ戻る問題を修正しました。

## [0.5.1] - 2026-08-16

### Changed

- 使用されていない非ストリーミングのチャット処理を削除し、ツール呼び出し関連のテストを実運用のストリーミング処理へ統合しました。

### Fixed

- `server.log`を`OLLAMA_WEB_DATA_DIR`の親ではなくデータディレクトリ直下へ保存し、アプリケーションの起動・終了に合わせてログハンドラーを開閉するようにしました。
- DuckDuckGo検索で発生した例外を空の検索結果として握りつぶさず、検索語を記録しない診断ログを残してツール実行層へ伝播するようにしました。

## [0.5.0] - 2026-08-16

### Added

- タグごとのGitHub Releaseノートを管理するため、`CHANGELOG.md`を追加しました。

### Changed

- READMEの冒頭説明と主な特徴を整理し、個人利用向けの軽量なWeb UIであること、Dockerや仮想マシンを使わずホストOS上で直接動作することを明確にしました。

### Fixed

- Clipboard APIを利用できないHTTP接続や権限拒否時に、コピーボタンが無反応に見える問題を修正。失敗時は警告アイコンを一時表示し、詳細をブラウザコンソールへ記録するようにしました。
- ツールラウンド上限到達後の最終回答生成でツール定義を渡していた問題を修正。標準ツールとMCPツールを無効化し、それまでの調査結果だけから回答を生成するようにしました。
- 新しいsetuptoolsでライセンスメタデータが重複してパッケージをビルドできない問題を修正しました。
- wheelおよびsource distributionにWeb UI、翻訳、プロンプトの実行時アセットが含まれない問題を修正しました。

## [0.4.5] - 2026-08-02

### Added

- `scrape_url`、`pdf_to_text`、`search_and_fetch` に継続トークン方式のページングを追加しました。

### Fixed

- `search_and_fetch` のページングトークンが、長い検索結果によって欠損・破損する問題を修正しました。

## [0.4.4] - 2026-07-09

### Fixed

- Ollamaが空のレスポンスを返した場合に再試行するようにしました。

## [0.4.3] - 2026-07-09

### Added

- チャットメッセージのコピー機能を追加しました。
- コンテキストのトークン消費量を表示するリングゲージを追加しました。
- 英語版READMEを追加しました。

### Changed

- LLMのストリーミング中でも、ユーザーがチャット履歴をスクロールしやすいように自動スクロールを調整しました。

### Fixed

- 停止ボタンを押した後にメッセージを送信できなくなる問題を修正しました。
- トークン消費量リングゲージの表示タイミングを調整しました。

## [0.4.2] - 2026-06-28

### Fixed

- モデル名にスラッシュが含まれる場合、モデル情報取得時にStarlette側でパスが正しく処理されない問題を修正しました。
- 上記の影響で、対応モデルでもthinkingトグルが無効になる問題を修正しました。

## [0.4.1] - 2026-06-28

### Added

- システムプロンプトへローカル時刻を注入する機能を追加しました。

## [0.4.0] - 2026-06-27

### Added

- UIの表示言語を切り替える多言語対応を追加しました。
- UIとOllamaの一般設定画面を追加しました。
- システムプロンプト、temperature、コンテキスト長などの設定を`settings.json`へ保存できるようにしました。

### Fixed

- MCP設定画面の言語切り替え表示を修正しました。
- CSPによってインラインJavaScriptがブロックされる問題を修正しました。
- Ollama詳細設定のプレースホルダーを修正しました。

### Changed

- READMEの画面イメージを更新しました。

## [0.3.3] - 2026-06-27

### Security

- クライアント側URLリダイレクトに関するコードスキャンの指摘へ対応しました。

## [0.3.2] - 2026-06-27

### Changed

- MarkdownのHTML変換をレスポンス完了時にまとめて行うようにし、チャットのレンダリング処理を最適化しました。

## [0.3.1] - 2026-06-27

### Changed

- MCPツール名による不要な実行制限を削除しました。
- READMEのスクリーンショットをGIFへ変更し、表示サイズを調整しました。

## [0.3.0] - 2026-06-27

### Added

- stdioおよびStreamable HTTPのMCPサーバーからツールを利用するMCPクライアント機能を追加しました。

### Security

- MCPサーバー設定と実行処理をセキュア化しました。

### Fixed

- MCP stdioサーバーの作業ディレクトリに関する不整合を修正しました。

## [0.2.0] - 2026-06-25

### Added

- Ollamaモデルのthinking、tools、vision対応状況を取得し、UIとリクエストをモデル能力に応じて切り替える機能を追加しました。
- READMEへスクリーンショットを追加しました。

### Fixed

- チャット履歴のクリアボタンに関する問題を修正しました。
- レスポンシブ表示時にヘッダーのレイアウトが崩れる問題を修正しました。

## [0.1.0] - 2026-06-25

### Added

- Ollamaモデルとのストリーミングチャットを追加しました。
- 会話セッションの保存と左サイドバーからの管理を追加しました。
- ファイル添付、ファイル検索・取得、PDFテキスト抽出を追加しました。
- visionモデル向けの画像添付処理を追加しました。
- Web検索とURLスクレイピングを追加しました。
- 数式レンダリングを追加しました。
- デスクトップとモバイル向けのレスポンシブUIを追加しました。

### Security

- Web UIのセキュリティヘッダーと表示処理を強化しました。

[Unreleased]: https://github.com/takashi000/ollama-web/compare/v0.5.4...HEAD
[0.5.4]: https://github.com/takashi000/ollama-web/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/takashi000/ollama-web/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/takashi000/ollama-web/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/takashi000/ollama-web/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/takashi000/ollama-web/compare/v0.4.5...v0.5.0
[0.4.5]: https://github.com/takashi000/ollama-web/compare/v0.4.4...v0.4.5
[0.4.4]: https://github.com/takashi000/ollama-web/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/takashi000/ollama-web/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/takashi000/ollama-web/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/takashi000/ollama-web/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/takashi000/ollama-web/compare/v0.3.3...v0.4.0
[0.3.3]: https://github.com/takashi000/ollama-web/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/takashi000/ollama-web/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/takashi000/ollama-web/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/takashi000/ollama-web/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/takashi000/ollama-web/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/takashi000/ollama-web/releases/tag/v0.1.0
