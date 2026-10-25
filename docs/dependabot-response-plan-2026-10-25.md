# Dependabot alerts 対応方針（2026-10-25 JST）

> これは初回調査時点の記録です。Python 3.11への移行案とPR分割案は、
> ユーザー指定のPython >=3.14への移行・1PRでの全件対応に置き換えました。
> 現在の実装方針・実測結果・完了ゲートは[互換性・検証記録](python314-security-validation.md)を参照してください。

対象: [takashi000/ollama-web](https://github.com/takashi000/ollama-web/security/dependabot)。
GitHubの既定ブランチはmain。コード照合はローカルmain（8be7dfa）で実施。
アラートはGitHub APIから取得した現在のopen状態であり、実行環境のインストール済みパッケージを監査した結果ではない。

## 結論

45件すべてがuv.lockのruntime依存に対する指摘。Critical 6件、High 26件、Medium 13件。
更新対象は3パッケージに集約できる。Pillow/pillowは同一パッケージとして集計した。

| 優先度 | パッケージ | 件数（Critical / High / Medium） | ローカルlock | APIが示す修正版 | 方針 |
| --- | --- | --- | --- | --- | --- |
| 最優先 | Pillow（直接依存） | 41（5 / 23 / 13） | 12.3.0 | 13.0.0 | Python最低要件を3.11に上げ、Pillowも更新 |
| 最優先・先行可能 | certifi（間接依存） | 1（1 / 0 / 0） | 2026.6.17 | 2026.10.19 | TLSの信頼ストア更新を先行 |
| 高・先行可能 | pypdf（直接依存） | 3（0 / 3 / 0） | 6.21.1 | 6.21.2 | 同一メジャー内で更新 |

上記の修正版に更新すると、取得時点の45件の脆弱範囲を外れる見込み。最終判断はマージ後のDependabot再解析で行う。

## コード上の影響と到達条件

### Pillow

- `src/ollama_web/tools/helper/image.py`の`resize_image()`は外部由来のバイト列を`Image.open(BytesIO(data))`で開き、RGB変換・リサイズ・再保存する。呼び出し元は`routes/chat.py`と`llm.py`。
- 入力形式を`Image.open(..., formats=...)`で制限していない。出力形式の拡張子マップは入力デコーダの制限にならない。JPEG 2000などの自動判定に関わる#88も考慮する。
- #76はTIFFをBytesIOへ保存する経路の指摘。`.tif`/`.tiff`をTIFFに再保存する実装と重なる。ただしRGB変換後に再保存しており、個別の悪用成立は未検証。
- #49はJPEGの`draft()`/`thumbnail()`による縮小デコードが成立条件。現実装の`convert()`→`resize()`ではその経路を使用していない。
- #81は複数フレーム間のモード遷移が条件。現実装はフレームの列挙・`seek()`をしていない。
- #93はFPXデコーダ用の任意依存`olefile`が条件。uv.lockには`olefile`がない。別途導入した実環境については未確認。
- #59はGhostscriptへ渡す入力ファイル名の注入が条件。現実装はファイル名付きオブジェクトではなくBytesIOを渡すため、その条件とは異なる。
- フォント描画・Arrow出力・PillowによるPDF追記などは調べたアプリコードに使用がない。これらの条件差は優先順位の補足とし、パッケージ更新でまとめて解消する。
- ピクセル数制限や例外捕捉だけでは、ネイティブコードのメモリ破壊や無限ループを防げない。

### pypdf

`tools/helper/pdf.py`の`PdfReader`、ページアクセス、テキスト抽出が対象。
添付PDFのプレビュー（`routes/sessions.py`）、URL取得（`tools/fetch.py`）、PDFツール（`tools/pdf.py`）から到達する。
#89〜#91は不正xrefや存在しないオブジェクトの反復参照による長時間処理。ファイルサイズ・ページ数・文字数の制限だけでは解析内部のCPU消費を抑えきれない。
特にアップロード処理内にも同期解析があるため、サービスの応答性への影響を重視する。

### certifi

uv.lock上でhttpx/httpcoreが依存するCAバンドル。#58は侵害された中間CA鍵につながるemSignルートの信頼に関する指摘で、攻撃には経路上での介入などの条件がある。
アプリのHTTPクライアントに関係するため先行更新する。APIの推奨どおり2026.10.19以降を採用し、配置済み環境・コンテナも再構築する。
実行環境で独自CAストアを設定しているかは未確認。

## 実装を進める順序

### PR 1: certifi / pypdfを先行更新

1. `pyproject.toml`のpypdf下限を`pypdf>=6.21.2,<7`へ変更。
2. 既存のセキュリティ下限制約の方針に合わせ、`certifi>=2026.10.19`を追加。通常のpipインストールにも適用するためで、uv専用の制約にはしない。
3. `uv lock --upgrade-package certifi --upgrade-package pypdf`でlockを再生成し、無関係な更新がないか確認。
4. PDF添付プレビュー、ページ継続、異常PDFのエラー処理、HTTP取得を確認して先行リリース。

### PR 2: Python要件変更とPillow更新

[Pillow 13.0.0の公式リリースノート](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html#python-3-10)でPython 3.10サポート終了を確認した。
現状の`requires-python = ">=3.10"`のままでは、全対応Python向けにPillow 13の下限を設けられない。

1. `requires-python`を`>=3.11`、Pillowを`Pillow>=13.0.0,<14`へ変更。
2. Python 3.10のclassifierとCIジョブを削除し、README等の対応バージョン記述も揃える。
3. `uv lock --upgrade-package pillow`でlockを再生成。Python 3.10用に脆弱な旧版が残る条件分岐は設けない。
4. Python 3.10利用者向けに移行手順・リリースノートを用意。サポート範囲の変更として明示する。
5. PNG/JPEG/GIF/BMP/WebP/TIFFのRGB変換・縮小・再保存、透過画像、複数フレーム画像、壊れた入力、ピクセル上限を確認。

Python 3.10維持が必須の場合は、上流のバックポート有無の追加調査または画像処理の分離が必要。
今回のAPIは41件すべてで13.0.0を修正版として示しており、3.10用旧版を残して全件解消とは扱わない。

### 検証と完了条件

- 既存の`.github/workflows/dependency-security.yml`を使い、各PRで対応OS/Pythonの`uv sync --locked --extra dev`、`uv pip check`、pytest、ruff、mypy、wheelビルドと通常pipインストール検証を通す。
- 既存の`tests/test_tools.py`、`tests/test_security.py`等に加え、上記の利用経路を確認。悪性入力の回帰検証を行う場合はタイムアウト付きの隔離プロセスで実施する。
- mainへマージ後、実行環境を更新・再起動してから、同じgh APIを再実行し、45件の解消と新規アラートを確認する。
- 後続改善として入力デコーダの許可リスト、画像/PDF処理のプロセス分離と時間・メモリ上限を検討する。更新が完了するまで未信頼画像の処理を制限できるとよい。

今回は調査資料の作成までを実施。依存定義・lock・アプリコード・GitHub上のアラート状態は変更せず、更新後テストも未実施。

## 再取得コマンドと取得データ

```powershell
gh api --paginate 'repos/takashi000/ollama-web/dependabot/alerts?state=open' --slurp
```

`--slurp`の戻り値はページごとの配列を含む二重配列。集計前に平坦化する。
全レスポンスは[取得JSON](dependabot-alerts-open-2026-10-25.json)に保存。
重大度・修正版は取得APIを基準とした。上流リリースノートの重大度と異なる場合がある。

追加確認: [pypdf 6.21.2](https://pypi.org/project/pypdf/6.21.2/)、[certifi 2026.10.19](https://pypi.org/project/certifi/2026.10.19/)。

## 全45件の一覧

以下のリンクは対象リポジトリの個別アラート。各件のGHSAと影響範囲も併記する。

| Alert | Package | Severity | GHSA | 影響バージョン | 修正版 | 内容 |
| --- | --- | --- | --- | --- | --- | --- |
| [#49](https://github.com/takashi000/ollama-web/security/dependabot/49) | Pillow | critical | [GHSA-8g5g-8vmr-9782](https://github.com/advisories/GHSA-8g5g-8vmr-9782) | <= 12.3.0 | 13.0.0 | Pillow: Heap buffer overflow in ImagingJpegDecode |
| [#50](https://github.com/takashi000/ollama-web/security/dependabot/50) | Pillow | high | [GHSA-58r4-6rv3-gfh8](https://github.com/advisories/GHSA-58r4-6rv3-gfh8) | <= 12.3.0 | 13.0.0 | Pillow: Out-of-bounds access in _imagingmorph on a zero-width image |
| [#51](https://github.com/takashi000/ollama-web/security/dependabot/51) | Pillow | high | [GHSA-pchr-8j38-3x4p](https://github.com/advisories/GHSA-pchr-8j38-3x4p) | <= 12.3.0 | 13.0.0 | Pillow: TIFF encoder ColorMap plane-size mismatch causes a heap out-of-bounds read |
| [#52](https://github.com/takashi000/ollama-web/security/dependabot/52) | Pillow | high | [GHSA-65qm-jvmh-6pqg](https://github.com/advisories/GHSA-65qm-jvmh-6pqg) | >= 9.2.0, < 13.0.0 | 13.0.0 | Pillow: GIF seek keeps rejected size after DecompressionBombError, allowing DoS |
| [#53](https://github.com/takashi000/ollama-web/security/dependabot/53) | pillow | medium | [GHSA-462x-pgg6-55fp](https://github.com/advisories/GHSA-462x-pgg6-55fp) | >= 8.2.0, < 13.0.0 | 13.0.0 | Pillow tiled-TIFF decoder reads past tile buffer in copy4 (Unpack.c:1290) |
| [#54](https://github.com/takashi000/ollama-web/security/dependabot/54) | Pillow | high | [GHSA-c6hw-5grf-ff96](https://github.com/advisories/GHSA-c6hw-5grf-ff96) | >= 8.1.0, < 13.0.0 | 13.0.0 | Pillow: OOB read in TiffDecode.c tile and RGBA paths leaks heap memory into pixels |
| [#55](https://github.com/takashi000/ollama-web/security/dependabot/55) | Pillow | high | [GHSA-q45j-58j2-45rg](https://github.com/advisories/GHSA-q45j-58j2-45rg) | >= 5.3.0, < 13.0.0 | 13.0.0 | Pillow: TiffDecode returns uninitialized memory as pixels for short JPEG strips |
| [#56](https://github.com/takashi000/ollama-web/security/dependabot/56) | pillow | high | [GHSA-p4wg-p73j-c528](https://github.com/advisories/GHSA-p4wg-p73j-c528) | >= 5.1.0, < 13.0.0 | 13.0.0 | Pillow: Unbounded recursion parsing a PDF page tree or trailer chain (incomplete fix for CVE-2026-42310) |
| [#57](https://github.com/takashi000/ollama-web/security/dependabot/57) | Pillow | medium | [GHSA-vx6w-vv9f-6gvg](https://github.com/advisories/GHSA-vx6w-vv9f-6gvg) | >= 2.3.0, <= 12.3.0 | 13.0.0 | Pillow: EPS load(scale) lets Ghostscript render past the decompression bomb limit |
| [#58](https://github.com/takashi000/ollama-web/security/dependabot/58) | certifi | critical | [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2) | >= 2019.03.09, < 2026.10.19 | 2026.10.19 | certifi bundles emSign root CAs with compromised intermediate CA keys |
| [#59](https://github.com/takashi000/ollama-web/security/dependabot/59) | Pillow | critical | [GHSA-w889-685w-c8fh](https://github.com/advisories/GHSA-w889-685w-c8fh) | >= 2.6.0, < 13.0.0 | 13.0.0 | Pillow: Argument Injection in Ghostscript call, potentially leading into RCE |
| [#60](https://github.com/takashi000/ollama-web/security/dependabot/60) | Pillow | medium | [GHSA-4558-3h8r-v8xp](https://github.com/advisories/GHSA-4558-3h8r-v8xp) | <= 12.3.0 | 13.0.0 | Pillow: P;2L and P;4L decoders may read 1 to 3 bytes past the passed buffer size |
| [#61](https://github.com/takashi000/ollama-web/security/dependabot/61) | Pillow | high | [GHSA-7mwm-ghpf-9vrm](https://github.com/advisories/GHSA-7mwm-ghpf-9vrm) | <= 12.3.0 | 13.0.0 | Pillow: Double free in ImagingScaleAffine on calloc failure via Image.transform |
| [#62](https://github.com/takashi000/ollama-web/security/dependabot/62) | pillow | high | [GHSA-j4m9-6w36-pw47](https://github.com/advisories/GHSA-j4m9-6w36-pw47) | >= 10.0.0, < 13.0.0 | 13.0.0 | Pillow: Invalid free in FreeType glyph rendering when a stroker is active |
| [#63](https://github.com/takashi000/ollama-web/security/dependabot/63) | pillow | high | [GHSA-cp9v-662q-vvx9](https://github.com/advisories/GHSA-cp9v-662q-vvx9) | >= 12.0.0, < 13.0.0 | 13.0.0 | Pillow: NULL pointer dereference in Image.merge via an unvalidated band 0 |
| [#64](https://github.com/takashi000/ollama-web/security/dependabot/64) | Pillow | high | [GHSA-27h8-gf7j-v5hc](https://github.com/advisories/GHSA-27h8-gf7j-v5hc) | <= 12.3.0 | 13.0.0 | Pillow: NULL dereference in ImagingExpand via rank filters on zero-size images |
| [#65](https://github.com/takashi000/ollama-web/security/dependabot/65) | pillow | medium | [GHSA-fc9g-5qg7-mf45](https://github.com/advisories/GHSA-fc9g-5qg7-mf45) | >= 9.2.0, < 13.0.0 | 13.0.0 | Pillow: Quadratic GIF comment copying during save |
| [#66](https://github.com/takashi000/ollama-web/security/dependabot/66) | Pillow | high | [GHSA-22hg-r758-rrw9](https://github.com/advisories/GHSA-22hg-r758-rrw9) | >= 7.2.0, < 13.0.0 | 13.0.0 | Pillow: Infinite loop in MIC n_frames when the embedded TIFF |
| [#67](https://github.com/takashi000/ollama-web/security/dependabot/67) | Pillow | medium | [GHSA-74r2-7ff9-mjj2](https://github.com/advisories/GHSA-74r2-7ff9-mjj2) | >= 5.4.0, < 13.0.0 | 13.0.0 | Pillow: Quadratic GIF comment concatenation allows DoS in Image.open() |
| [#68](https://github.com/takashi000/ollama-web/security/dependabot/68) | Pillow | medium | [GHSA-rfgq-q3w2-pmv2](https://github.com/advisories/GHSA-rfgq-q3w2-pmv2) | >= 9.1.0, < 13.0.0 | 13.0.0 | Pillow: Image.quantize() with empty palette leaks uninitialized stack memory |
| [#69](https://github.com/takashi000/ollama-web/security/dependabot/69) | Pillow | medium | [GHSA-xgh8-jp6q-p5x8](https://github.com/advisories/GHSA-xgh8-jp6q-p5x8) | <= 12.3.0 | 13.0.0 | Pillow: ReDoS in XbmImagePlugin: 512-byte file stalls Image.open() for minutes |
| [#70](https://github.com/takashi000/ollama-web/security/dependabot/70) | Pillow | medium | [GHSA-254f-3936-288h](https://github.com/advisories/GHSA-254f-3936-288h) | >= 5.1.0, < 13.0.0 | 13.0.0 | Pillow: PdfParser can recurse without limit in get_value() and linearize_page_tree() |
| [#71](https://github.com/takashi000/ollama-web/security/dependabot/71) | Pillow | high | [GHSA-jg86-v3jv-hrpm](https://github.com/advisories/GHSA-jg86-v3jv-hrpm) | >= 5.1.0, < 13.0.0 | 13.0.0 | Pillow: Quadratic-time DoS in PDF save_all (PdfParser.next_object_id) |
| [#72](https://github.com/takashi000/ollama-web/security/dependabot/72) | pillow | high | [GHSA-gg2r-wvfm-q9rx](https://github.com/advisories/GHSA-gg2r-wvfm-q9rx) | >= 4.1.0, < 13.0.0 | 13.0.0 | Pillow: MSP RLE decoder expands a tiny file into unbounded memory |
| [#73](https://github.com/takashi000/ollama-web/security/dependabot/73) | Pillow | high | [GHSA-5xm2-6676-qcp9](https://github.com/advisories/GHSA-5xm2-6676-qcp9) | >= 8.1.0, < 13.0.0 | 13.0.0 | Pillow: APNG background disposal allocates full canvas before decompression bomb check |
| [#74](https://github.com/takashi000/ollama-web/security/dependabot/74) | Pillow | high | [GHSA-f2g6-542v-9j58](https://github.com/advisories/GHSA-f2g6-542v-9j58) | >= 5.1.0, < 13.0.0 | 13.0.0 | Pillow: Infinite loop in PdfParser.rewrite_pages on page tree Parent cycle |
| [#75](https://github.com/takashi000/ollama-web/security/dependabot/75) | pillow | medium | [GHSA-4rr5-89j8-mwwm](https://github.com/advisories/GHSA-4rr5-89j8-mwwm) | >= 11.3.0, < 13.0.0 | 13.0.0 | Pillow: Heap use-after-free when encoding TIFF to an in-memory buffer |
| [#76](https://github.com/takashi000/ollama-web/security/dependabot/76) | Pillow | critical | [GHSA-332p-7hrc-92wf](https://github.com/advisories/GHSA-332p-7hrc-92wf) | >= 2.0.0, < 13.0.0 | 13.0.0 | Pillow: Heap buffer overflow in _tiffWriteProc when saving TIFF to BytesIO |
| [#77](https://github.com/takashi000/ollama-web/security/dependabot/77) | Pillow | high | [GHSA-xvh5-g6gv-559v](https://github.com/advisories/GHSA-xvh5-g6gv-559v) | <= 12.3.0 | 13.0.0 |  Pillow: Memory disclosure and segfault in libtiff encoder via crafted TIFF tags |
| [#78](https://github.com/takashi000/ollama-web/security/dependabot/78) | Pillow | medium | [GHSA-p8hx-v2p6-rp84](https://github.com/advisories/GHSA-p8hx-v2p6-rp84) | < 13.0.0 | 13.0.0 | Pillow: Decompression-bomb protection bypass via multi-frame seek() in MPO and DCX (MpoImageFile.seek / DcxImageFile.seek) |
| [#79](https://github.com/takashi000/ollama-web/security/dependabot/79) | pillow | high | [GHSA-g842-9rc4-69pw](https://github.com/advisories/GHSA-g842-9rc4-69pw) | < 13.0.0 | 13.0.0 | Pillow: Decompression bomb check bypass in SpiderImagePlugin.seek() leads to unbounded allocation DoS |
| [#80](https://github.com/takashi000/ollama-web/security/dependabot/80) | pillow | medium | [GHSA-24rc-8hj2-v98c](https://github.com/advisories/GHSA-24rc-8hj2-v98c) | >= 9.1.0, < 13.0.0 | 13.0.0 | Pillow: BMP RLE decoder memory-amplification DoS via unbounded delta escape / absolute mode |
| [#81](https://github.com/takashi000/ollama-web/security/dependabot/81) | Pillow | critical | [GHSA-v9pv-5gc8-w2m6](https://github.com/advisories/GHSA-v9pv-5gc8-w2m6) | >= 11.0.0, < 13.0.0 | 13.0.0 | Pillow multi-frame loaders can reuse stale native image storage after mode transitions |
| [#82](https://github.com/takashi000/ollama-web/security/dependabot/82) | Pillow | high | [GHSA-mrfp-p49p-3hf2](https://github.com/advisories/GHSA-mrfp-p49p-3hf2) | >= 5.1.0, < 13.0.0 | 13.0.0 | Pillow PDF append accepts malformed shared page-tree DAG causing resource exhaustion |
| [#83](https://github.com/takashi000/ollama-web/security/dependabot/83) | pillow | medium | [GHSA-4rm8-3hmm-39fg](https://github.com/advisories/GHSA-4rm8-3hmm-39fg) | < 13.0.0 | 13.0.0 | Pillow: Reachable undefined behavior in native integer operations, potentially leading to denial of service or logic integrity compromise |
| [#84](https://github.com/takashi000/ollama-web/security/dependabot/84) | Pillow | high | [GHSA-3j72-p6qg-6xp7](https://github.com/advisories/GHSA-3j72-p6qg-6xp7) | < 13.0.0 | 13.0.0 | Pillow: TarIO negative size field causes infinite CPU-spin loop |
| [#85](https://github.com/takashi000/ollama-web/security/dependabot/85) | Pillow | high | [GHSA-g4qv-2jxx-769h](https://github.com/advisories/GHSA-g4qv-2jxx-769h) | >= 11.0.0, < 13.0.0 | 13.0.0 | Pillow: PNG eXIf chunk makes EXIF parsing run in quadratic time |
| [#86](https://github.com/takashi000/ollama-web/security/dependabot/86) | Pillow | high | [GHSA-9v9v-9qvf-h2hj](https://github.com/advisories/GHSA-9v9v-9qvf-h2hj) | < 13.0.0 | 13.0.0 | Pillow: Heap over-read in WMF/EMF rendering via 16-bit dimension truncation (Windows builds) |
| [#87](https://github.com/takashi000/ollama-web/security/dependabot/87) | Pillow | high | [GHSA-g7cr-3p24-7wf7](https://github.com/advisories/GHSA-g7cr-3p24-7wf7) | >= 9.4.0, < 13.0.0 | 13.0.0 | Pillow: Heap buffer overflow writing JPEG comment after ICC profile fills buffer |
| [#88](https://github.com/takashi000/ollama-web/security/dependabot/88) | pillow | high | [GHSA-78g4-xqwv-q3g8](https://github.com/advisories/GHSA-78g4-xqwv-q3g8) | >= 11.1.0, < 13.0.0 | 13.0.0 | Pillow: Bundled libopenjp2 2.5.4 in all wheels since 11.1.0 carries OSV-2025-219 heap-buffer-overflow write reachable from Image.open().load() |
| [#89](https://github.com/takashi000/ollama-web/security/dependabot/89) | pypdf | high | [GHSA-gmh7-3g9r-mjh5](https://github.com/advisories/GHSA-gmh7-3g9r-mjh5) | < 6.21.2 | 6.21.2 | pypdf: Possible long runtimes for large xref table header limits |
| [#90](https://github.com/takashi000/ollama-web/security/dependabot/90) | pypdf | high | [GHSA-jr96-gvgh-j3r4](https://github.com/advisories/GHSA-jr96-gvgh-j3r4) | < 6.21.2 | 6.21.2 | pypdf: Possible long runtimes for repeated malformed cross-reference entries (Follow-up) |
| [#91](https://github.com/takashi000/ollama-web/security/dependabot/91) | pypdf | high | [GHSA-6c7f-9265-g8fw](https://github.com/advisories/GHSA-6c7f-9265-g8fw) | < 6.21.2 | 6.21.2 | pypdf: Possible long runtimes for repeated references to the same missing object |
| [#92](https://github.com/takashi000/ollama-web/security/dependabot/92) | Pillow | medium | [GHSA-654x-cwwv-5j9c](https://github.com/advisories/GHSA-654x-cwwv-5j9c) | >= 11.2.1, < 13.0.0 | 13.0.0 | Pillow: Arrow export leaks stale heap memory when PILLOW_ALIGNMENT > 1 |
| [#93](https://github.com/takashi000/ollama-web/security/dependabot/93) | pillow | critical | [GHSA-5x8w-8jj5-q52h](https://github.com/advisories/GHSA-5x8w-8jj5-q52h) | <= 12.3.0 | 13.0.0 | Pillow: Heap buffer overflow in FlashPix (FPX) JPEG decoding leads to code execution |
