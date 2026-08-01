# Gmail MCP

Gmail・メール検索に関する共通の作法。**`~/.claude/GMAIL-MCP-local.md` があれば併せて読む**（無ければスキップ）。使う MCP サーバー名・アカウント・下書き作成の個別ルールはそちらにある。

## 検索の原則

**メール検索は必ずメールアドレスで絞り込む（最重要）：**
- `to:address@domain` / `from:address@domain` / `cc:address@domain` を使う
- 人名キーワードや `to:名前` は表記揺れ・同姓同名・引用スニペットのヒットで取り違えるため使わない
- **アドレスが分かっている関係者は先にアドレスで検索**（組織の名簿正本・llm-wiki entity・過去メールの署名から引く）。期間・キーワードはその後の絞り込みに使う。
- **アドレス未確認の相手は、まずキーワードで検索→ヒットしたメールの `From:` を開いてアドレスを確定→以降はそのアドレスで検索**、という順で自力で調べる（2026-07-25 決定）。ユーザーに確認を求めて止まらない。引用・cc・スニペットは帰属の根拠にならないので、人物への帰属を主張する前に必ず `From:` を開く。

**アドレス解決の順序（確定結果を台帳に蓄積し次回セッションで再利用する）：**
1. 作業中プロジェクトの `CLAUDE.md` の `## メールアドレス台帳`（既にコンテキスト内。コスト0）
2. 既存の名簿 CSV（組織の名簿正本があればそれ）・プロジェクトの `_reference/email_addresses.csv`
3. llm-wiki entity の summary/aliases（人物 entity がある場合）
4. 無ければキーワード検索 → `From:` で確定 → **確定した直後にプロジェクトの台帳へ即時追記**（原則 CLAUDE.md の `## メールアドレス台帳` に `` `addr` `` — 氏名（所属）確認日 の箇条書き。名簿 CSV でカバー済みの相手は二重に書かない。20件級に育つ／スクリプトで扱う場合は `_reference/email_addresses.csv` に置く）

## Token 最小化の必須手順

1. **`search_emails` で Gmail 検索構文を使って絞り込む**（メタデータのみ返り軽量）
2. 結果から必要なメールIDを特定
3. **`read_email` でピンポイントに本文取得**（重いので最小限に）
4. `list_emails`（無差別羅列）は原則使わない。「最新N件を見たい」等の明示要求時のみ

絞り込み条件が曖昧な場合は、検索を実行する前にユーザーに条件（差出人・期間・キーワード等）を確認すること。

## Gmail 検索構文

```
from:nnh.go.jp after:2026/04/01 before:2026/05/20
subject:"治験" has:attachment filename:pdf
to:me is:unread label:重要
larger:1M -from:notification@github.com
"aCRF" OR "EDC"
```

主な演算子：
- `from:` `to:` `cc:` `subject:`
- `after:YYYY/MM/DD` `before:YYYY/MM/DD` `older_than:7d` `newer_than:1m`
  - **注意**: `after:`/`before:` は境界日を含まない（排他的）。当日のメールを検索するには1日前を指定する（例: 今日=2026/07/01 なら `after:2026/06/30`）。当日を確実に含めたい場合は `newer_than:1d` を使う方が確実。
- `has:attachment` `filename:pdf` `larger:1M`
- `label:` `is:unread` `is:starred` `in:inbox` `in:sent`
- `"完全一致フレーズ"` `OR` `-除外語`
