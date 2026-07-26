# transcribe-meeting セットアップ

依存パッケージ：

```bash
pip3 install google-genai
brew install ffmpeg          # 音声分割に ffmpeg / ffprobe が必要
```

Gemini APIキーの取得と設定：

1. [Google AI Studio](https://aistudio.google.com/apikey) で「Create API key」
2. キー（`AIza...`）を環境変数に設定：`export GEMINI_API_KEY="..."`（`~/.zshrc` 等に追記して永続化）
3. または `~/.config/claude-toolkit/gemini-api-key` にキーだけを書いたファイルを置く（環境変数未設定時のフォールバック）

GUI選択モード（`--gui`）を使う場合は `tkinter` が必要。

無料枠の構造・本日消費のローカル集計・課金キーへの切り替えは `quota-and-billing.md`。
