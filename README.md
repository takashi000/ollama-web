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

# テストの実行
pytest

# リント
ruff check .

# 型チェック
mypy src/
```

## ライセンス

MIT