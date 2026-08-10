# セッション終了ルーチン

「セッションを終わります」「終えます」「終了します」等の発言を検知したら、以下を**確認なしに順番に実行**する。

**`~/.claude/SESSION-END-local.md` があれば併せて読み、そこで指定された追加ステップ・上書きを指示された位置に挿入して実行する**（無ければスキップ）。個人・組織に固有の手順はそちらにある。

## Step 1: テンポラリファイルの自動整理

> ポリシー詳細: `~/.claude/conventions/session-end-auto-file-move.md`

- セッション中に `~/Desktop/` または `~/Downloads/` 内のファイルを**参照した場合のみ**実行（参照なければスキップ）
- 参照したファイルを**確認なし**で `<作業プロジェクト>/attachments/YYYYMMDD/` に `mv`
- 移動後、移動元の親フォルダが空になった場合は削除（`~/Desktop/` と `~/Downloads/` 本体は削除しない）
- 移動したファイル一覧を作業ログ（Step 2）に記録する
- 生ファイル（HEIC等）は `.gitignore` 対象なのでgit管理外でよい
- `docs/` は作成ドキュメントの置き場なので、生ファイルの移動先には使わない

## Step 2: 作業ログの作成・更新
- 日付付きファイル `docs/work-logs/YYYYMMDD-work-log.md` に当日の作業内容をまとめる。既存の当日ログがあれば追記、なければ新規作成
- **【必須・recency bias 対策】ログは「現在の文脈の記憶」からではなく、必ず当セッションの全トランスクリプト(JSONL)から作業トレースを抽出して再構成する。** `/compact` 後はセッション前半が要約で圧縮され記憶が後半に偏るため、記憶だけで書くと前半作業が欠落する。次のコマンドで全期間の成果物・調査・意思決定を抽出してから書く：
  ```bash
  TX=$(ls -t ~/.claude/projects/"$(pwd | sed 's#/#-#g')"/*.jsonl 2>/dev/null | head -1)
  jq -r 'select(.type=="assistant").message.content[]?|select(.type=="tool_use" and (.name=="Write" or .name=="Edit"))|.name+"  "+.input.file_path' "$TX" | sort -u      # 作成/編集ファイル
  jq -r 'select(.type=="assistant").message.content[]?|select(.type=="tool_use" and (.name=="WebSearch" or .name=="WebFetch"))|(.input.query // .input.url)' "$TX"      # 調査
  jq -r 'select(.type=="assistant").message.content[]?|select(.type=="tool_use" and .name=="AskUserQuestion")|.input.questions[]?.header' "$TX"                            # 意思決定
  ```
  抽出結果を**作業ストリーム単位**でまとめ、セッション全期間を網羅する。
- 内容: 概要・決定事項・**当セッションで作成/更新したファイル**・次回アクション
- **参照のみのファイルは原則ログに列挙しない**（決定の根拠として必要な場合は本文中で引用するに留める）

### Step 2b: アクションアイテムの専用ファイル化
- **規約**：積み残し（next action）は narrative ドキュメントに埋め込まず、専用ファイルに切り出して管理する。元の記載は専用ファイルへの**参照だけ**残し、各 `CLAUDE.md` から参照させる。配置・命名と導入判定の正本は `~/.claude/conventions/action-items-convention.md`。
- **セッション末の動作**：当セッションで新たなアクションアイテムが生じた／既存が narrative に埋め込まれたままの場合、規約の判定基準に照らして**確認なしに自動で**、専用ファイルを作成/更新し元を参照化する（判定が「作らない」なら work-log の次回アクションに残して終える）。帰属先サブプロジェクト・トピックが自明でないときだけユーザーに確認する。
- 完了済みは `[x]`、不要は削除。
- **`[x]` を付け終えたら、完了項目を隣の `action-items-archive.md` へ退避する**（未完了だけを残して見通しを保つ）。退避の手順・仕様は `~/.claude/conventions/action-items-convention.md` の「完了項目のアーカイブ」が正本。

## Step 3: 整合性確認
- プロジェクト内の関連ファイル間で寸法・数値・方針が一致しているか確認
- 作業がプロジェクト外（llm-wiki、他プロジェクト）に影響する場合はそちらも確認

## Step 4: llm-wiki 更新
- プロジェクト横断で有用な知識・判断・事実があれば `~/llm-wiki/` を更新
- **【人物・必須／PERSON-PROTOCOL R3】本セッションで関与・言及した各人物について、新事実（役割変更・接触記録・評価・訂正）を該当 `wiki/entities/<slug>.md` と `log.md` に漏れなく追記する。** 機微情報も wiki に記載し `sensitivity` ラベルで機密管理（R4）。該当人物が無ければスキップ可。

## Step 5: git commit & push
- 変更ファイルをステージング → コミット → push
- **「コミットしますか？」と聞かない**。セッション終了時は必ず commit & push する
- コミットメッセージは変更内容を端的に示す（例: `Add taro sprouting plan and 2026-05-09 work log`）

## Step 6: ユーザーへの操作依頼
- セッション内容を表す短い名前（kebab-case、例: `plant-taro-sprouting-plan`, `gas-member-sync-setup`）を決め、`/rename <名前>` の形で `pbcopy` に入れる（そのまま貼り付けて実行できるように）
- ユーザーへは**決めた名前を省略せず全文で示す**。`/rename ...` のように省略しない——クリップボードは貼り付け前に他のコピー操作で上書きされ得るため、省略形だと何を貼るべきだったか復元できなくなる（規約: `~/.claude/conventions/copy-paste-output-format.md`「pbcopy した内容は本文にも省略せずそのまま書く」）。以下の形で伝える：
```
`/rename plant-taro-sprouting-plan` をクリップボードからそのまま貼り付けて実行してください
```
（`plant-taro-sprouting-plan` は例。実際に決めた名前をそのまま入れる）
