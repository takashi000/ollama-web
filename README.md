# ollama-web

Ollama を用いた Web アプリケーションプロジェクト。

## セットアップ

```bash
# 仮想環境の作成
uv venv .venv

# 依存パッケージのインストール
uv pip install -r requirements.txt -p .venv

# 開発用パッケージのインストール
uv pip install -e ".[dev]" -p .venv
```

## プロジェクト構成

```
ollama-web/
├── src/
│   └── ollama_web/        # メインパッケージ
│       ├── __init__.py
│       └── main.py        # エントリポイント
├── tests/                 # テストコード
├── docs/                  # ドキュメント
├── scripts/               # スクリプト類
├── .gitignore
├── pyproject.toml         # プロジェクト設定 (ruff, pytest, mypy 等)
├── requirements.txt       # 本番依存パッケージ
└── README.md
```

## 使い方

```bash
# アプリケーションの実行
ollama-web

# LAN内のスマホ等からアクセスする場合
# PINを固定し、待受アドレスを明示してください
OLLAMA_WEB_PIN=123456 OLLAMA_WEB_HOST=0.0.0.0 ollama-web

# テストの実行
pytest

# リント
ruff check .

# 型チェック
mypy src/
```

## セキュリティ設定

- 既定の待受は `127.0.0.1` です。LAN公開する場合だけ `OLLAMA_WEB_HOST=0.0.0.0` を指定してください。
- `OLLAMA_WEB_PIN` 未設定時は起動ごとにランダムPINが表示されます。
- CORSは既定で無効です。必要な場合のみ `OLLAMA_WEB_ALLOWED_ORIGINS` に許可オリジンをカンマ区切りで指定してください。
- Cookie署名鍵は `OLLAMA_WEB_SECRET_KEY` で固定できます。

## ライセンス

MIT
