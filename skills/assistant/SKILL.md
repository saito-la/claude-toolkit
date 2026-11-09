---
name: assistant
description: 秘書の仕組み（台帳・予定表・Gmail・作業用の Google ドキュメントから、その日にやることを優先順に並べた today.md を作る）のインスタンスで、「init」（朝の一式）・「update」「updateして」（日中の today.md の作り直し）と言われたときに使う。assistant.json のあるフォルダ（インスタンス）のセッションでだけ動き、ほかのフォルダでは何もしない。秘書の仕組みを新しく導入したい・設定を変えたいときにも使う。
---

# assistant

秘書の仕組みのエンジン。このフォルダ（`~/.claude/skills/assistant`）にコードと手順書があり、使い手ごとの設定と出力はインスタンスのフォルダにある。

## 呼ばれたら

1. 作業ディレクトリから上へたどって `assistant.json` を探す。無ければ「秘書の仕組みのインスタンスのフォルダではないので何もしない」と伝えて終える（導入の相談なら下の「導入と設定」へ）
2. 「init」なら [docs/daily-and-update.md](docs/daily-and-update.md)「4. init の手順」、「update」なら同「6. update の手順」のとおりに進める。最初に同「2.2. 新しいセッションで行う」の文脈の大きさの確かめを行い、超えていたら止まる
3. 承認を挟まず、報告は最後に1回だけ

決まりの正本は [docs/design.md](docs/design.md)。インスタンスに `overview.md` があれば併せて読む。スクリプトはこのフォルダの `scripts/` にあり、インスタンスのフォルダを作業ディレクトリにして `node ~/.claude/skills/assistant/scripts/<名前>.mjs` で呼ぶ。

## 導入と設定

[README.md](README.md)「3. 導入」「4. 設定」。
