# Changelog

このプロジェクトの注目すべき変更は、このファイルに記録します。

形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) を参考にし、
バージョン番号は [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

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

[Unreleased]: https://github.com/takashi000/ollama-web/compare/v0.5.0...HEAD
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
