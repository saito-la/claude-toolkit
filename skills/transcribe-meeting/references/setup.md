# transcribe-meeting セットアップ

依存パッケージ：

```bash
pip3 install google-genai
brew install ffmpeg          # 音声分割に ffmpeg / ffprobe が必要
```

Homebrew が入らない Mac（管理者権限が無い端末など）では、npm レジストリだけで完結する経路を使う。

```bash
npm install -g @ffmpeg-installer/ffmpeg @ffprobe-installer/ffprobe
# postinstall（chmod u+x）が npm の allow-scripts に保留されるため手動で実行権を付ける
FF=$(node -e "console.log(require('$(npm root -g)/@ffmpeg-installer/ffmpeg').path)")
FP=$(node -e "console.log(require('$(npm root -g)/@ffprobe-installer/ffprobe').path)")
chmod u+x "$FF" "$FP"
mkdir -p ~/.local/bin
ln -sf "$FF" ~/.local/bin/ffmpeg && ln -sf "$FP" ~/.local/bin/ffprobe
```

落とし穴が2つある。postinstall が実行されずバイナリに実行権が付かないこと（`npm warn allow-scripts`）と、プラットフォーム別パッケージの実体が `@ffmpeg-installer/darwin-arm64` 直下ではなくネストした `node_modules` 配下にあること。後者があるため、パスは決め打ちせず `require(...).path` で解決する。`~/.local/bin` が PATH に入っていることを確認する。

Gemini APIキーの取得と設定：

1. [Google AI Studio](https://aistudio.google.com/apikey) で「Create API key」
2. キー（`AIza...`）を環境変数に設定：`export GEMINI_API_KEY="..."`（`~/.zshrc` 等に追記して永続化）
3. または `~/.config/claude-toolkit/gemini-api-key` にキーだけを書いたファイルを置く（環境変数未設定時のフォールバック）

GUI選択モード（`--gui`）を使う場合は `tkinter` が必要。

無料枠の構造・本日消費のローカル集計・課金キーへの切り替えは `quota-and-billing.md`。
