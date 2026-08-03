# claude-toolkit

Claude Code用の汎用Skill・規約集。プロジェクト非依存のツール群。

## 収録規約

`conventions/` は「どの組織・どの案件でも通用する作業規約」を置く。Claude Code から `~/.claude/conventions/<name>.md` として参照される前提で書き、個人・組織固有の絶対パス・実名・内部文書の節番号は含めない。

| 規約 | 内容 |
|---|---|
| [living-doc-structure](conventions/living-doc-structure.md) | 生きた文書（`overview.md` 等）の構成。見出しは名詞句のみ・連番は1始まり・履歴を本文に溜めない |
| [action-items-convention](conventions/action-items-convention.md) | `action-items.md` の運用と完了項目のアーカイブ |
| [sop-manual-authoring](conventions/sop-manual-authoring.md) | SOP・手順書・業務マニュアルの標準構成と記述作法 |
| [spreadsheet-structure](conventions/spreadsheet-structure.md) | スプレッドシート設計（入力層／表示層の分離・tidy table・権限境界でファイル分割） |
| [file-naming-and-folder-hygiene](conventions/file-naming-and-folder-hygiene.md) | 共有ファイル・フォルダの命名と配置 |
| [copy-paste-output-format](conventions/copy-paste-output-format.md) | コピペ前提テキストの出力作法（URL単独行・`.txt`/`pbcopy`） |
| [session-end-auto-file-move](conventions/session-end-auto-file-move.md) | セッション終了時のテンポラリファイル自動移動ポリシー |
| [email-draft-conventions](conventions/email-draft-conventions.md) | メール返信下書きの起案作法（起案フロー・下書きの単一性・スレッドと引用・宛先の決め方・送信前の確認） |
| [email-address-ledger](conventions/email-address-ledger.md) | 確認済みメールアドレスの索引の持ち方（既定はプロジェクトの `CLAUDE.md` に箇条書き・育てば CSV へ） |

配置は `install.py` が行う（下記「インストール」）。グローバル `~/.claude/CLAUDE.md` に「作業種別ごとの規約は `~/.claude/conventions/` が正本。着手前に該当ファイルを読む」の1行を置くと、Claude が作業種別に応じて読みに行く。取り込みは任意。

### メール規約の使い方

メール関連の3ファイルは役割が分かれている。**Claude がどの場面でどれを読むか**で覚えるのが早い。

- **`email-draft-conventions.md`（配布物）** — 返信の下書きを作るときの作法。誰にでも当てはまる部分だけが入っている。送信は必ず本人が行う・推敲で下書きを増やさない・引用を刈り込まない、といった規律
- **`email-draft-conventions-local.md`（自分で作る）** — 自分の文体。宛名の敬称・挨拶・結び・署名・「初稿から何を削るか」。**このファイルは配布物に含まれない**ので、上流を `git pull` しても上書きされない
- **`email-address-ledger.md`（配布物）** — 「誰のアドレスが何か」を確定するたびにプロジェクトの `CLAUDE.md` へ書き足す運用。メール検索は人名ではなくアドレスで絞らないと同姓同名・引用スニペットで取り違えるため

**使い込むほど価値が出るのは後者2つ**——つまり `-local` の文体ファイルと、各プロジェクトの台帳。ここが自分の資産として溜まる場所で、配布物側（この 2 ファイル）は誰が使っても同じ内容のまま更新されていく。

**育て方は次の3つの往復に尽きる。**

1. **初回に文体ファイルの初版を作る。** 推測で書かず、自分の送信済みメール直近数十通を Claude に走査させ、実際に使っている宛名・挨拶・結び・署名を数えさせる。一般的なビジネスメールの定型に寄せると実態より丁寧になり、毎回直すことになる。ある利用者の実測では、宛名の敬称は「先生」21件・「さん」19件・「様」3件で、一般的な既定である「様」がほぼ使われていなかった
2. **Claude の初稿を直したら、何を削ったかを1行足す。** 「相手が文脈を共有している社内通知では、理由説明を削って結論だけ書く」「面談前に確認事項リストを送らない——相手の負荷になり面談の主目的をぼかす」といった調子で、削った理由まで書く。次の初稿がそこから始まる
3. **メール検索で新しいアドレスを確定したら、その場で台帳へ追記する。** セッション末にまとめると取りこぼす。1行 `` `tanaka@example.co.jp` `` — 田中一郎（例社・渉外）2026-07-25確認 で足りる

この規約は、Gmail（MCP 経由）と Outlook（COM 経由）という配管のまったく違う2つの運用が独立に同じ結論へ到達した部分を抽出して作られている。したがって**特定のメールクライアントに縛られない**——自分の環境でどのコマンドを叩くかは、この規約の外側の話になる。

## 収録スキル

**グローバル配置**（`~/.claude/skills/` へ配置し全プロジェクトで発見させる）5件：

| スキル | 内容 |
|---|---|
| [markdown-export](skills/markdown-export/SKILL.md) | markdown → Word(.docx) / PDF 変換 |
| [markdown-to-gdocs](skills/markdown-to-gdocs/SKILL.md) | markdown/docx → Google Docs アップロード＋体裁適用（要・自分のGoogle Cloud OAuthセットアップ） |
| [transcribe-meeting](skills/transcribe-meeting/SKILL.md) | 会議録音 → 議事録3点セット（原文・ケバ取り版・凝縮版）自動生成（要・Gemini APIキー） |
| [mcp-setup](skills/mcp-setup/SKILL.md) | Claude CodeへのMCPサーバー接続・セットアップ手順を案内 |
| [format-prompt](skills/format-prompt/SKILL.md) | 粗いプロンプトを7ブロックの型に整形 |

**個別プロジェクト配置**（用途が特定プロジェクトに閉じるため、グローバルには置かない）5件：

| スキル | 内容 | 配置先の目安 |
|---|---|---|
| [email-to-calendar](skills/email-to-calendar/SKILL.md) | 開催案内メールから日時・場所・URLを抽出しGoogleカレンダーへ登録。差出人別ルールを自己蓄積し継続利用で精度向上（要・`RULES.example.md`を`RULES.md`にコピー） | 秘書業務を行うプロジェクト（例：CRPC秘書業務） |
| [schedule-reply](skills/schedule-reply/SKILL.md) | 日程調整メール(tonton/調整さん)にGoogleカレンダーの空きで自動回答（要・個別セットアップ、上級者向け） | 同上 |
| [interest-profile](skills/interest-profile/SKILL.md) | 会話履歴からユーザーの興味プロファイルを生成・更新 | 利用頻度が低いプロジェクトのみ |
| [meishi-rename](skills/meishi-rename/SKILL.md) | 名刺スキャンPDFのファイル名をOCR結果から整形 | 名刺整理を行うプロジェクト |
| [person-research](skills/person-research/SKILL.md) | 人物調査URLからレジストリを横断調査し根拠付きレポートを作成 | 個人環境ラッパー（command版）から参照される汎用ロジック。単体配置は通常不要 |

## インストール

```bash
git clone <このリポジトリ> ~/claude-toolkit
python3 ~/claude-toolkit/install.py
```

`install.py` が `skills/`・`conventions/`・`guides/`・`instructions/`・`tools/statusline/statusline.py` を `~/.claude/` へ配置し、`settings.json` に `statusLine` を追記する（既に設定済みなら壊さない）。POSIX では symlink、Windows では権限の都合でコピーになる。

配置後、Claude Codeが会話の文脈（「Wordにして」「PDFにして」等）から自動的にスキルを発見する。`instructions/` だけは置くだけでは読まれず、`~/.claude/CLAUDE.md` に `@instructions/<名前>` を足して初めて効く（必要な行はインストーラが最後に表示する）。

### 更新

```bash
git -C ~/claude-toolkit pull
python3 ~/claude-toolkit/install.py
```

**冪等で、消えたものが消える。** 何を配置したかを `~/.claude/.wired-by` に記録しておき、次回そこに無いものを撤去する。上流で廃止したスキル・規約が端末に残り続けることがない。ユーザーが自分の実体に差し替えたファイルは撤去せず残す。何が起きるかだけ見たいときは `--dry-run`。

symlink 配置なら中身の更新は `git pull` だけで反映される（再実行が要るのは、スキルの増減があったときと、コピー配置の Windows）。

### 配布リポジトリから呼ぶ場合

**配置ロジックはこのリポジトリの `install.py` が唯一の実装。** 配布側は置き方を書き直さず、submodule として抱えた本スクリプトを呼ぶ。

```bash
python3 vendor/claude-toolkit/install.py --label "<配布元の名前>"
```

主なオプションは `--root`（配布元。既定は本スクリプトの位置）・`--mode symlink|copy`・`--no-settings`（呼び出し側が `settings.json` を管理する場合）・`--force`・`--dry-run`。

1台のマシンに配布元が複数ある場合（正本 clone と、配布リポジトリの submodule が同居する場合）、**後から走った別の配布元による上書きは中止される。** 参照先が配布用スナップショットへ倒れると、正本を編集しても submodule を bump するまで反映されなくなるため。意図した切り替えなら `--force`、動作確認なら `HOME=$(mktemp -d) python3 install.py` でホームを分ける。

**個別プロジェクト配置**は上記グローバル標準に含めない。該当プロジェクトの `.claude/skills/<name>/` へ個別にsymlinkする。

- `schedule-reply` は Google Calendar/Gmail への書き込み・自動送信を伴うため、配置後も `skills/schedule-reply/scripts/README.md` に沿った個別セットアップ（`config.json`作成・Google認証・Playwright）が別途必要。
- `email-to-calendar` は初回のみ `skills/email-to-calendar/RULES.example.md` を同ディレクトリの `RULES.md` にコピーして使い始める（差出人別ルールが蓄積されるファイルのため`.gitignore`済み）。

## 依存関係

- `markdown-export`：pandoc, python-docx, lxml, pymupdf, Google Chrome（PDF生成）
- `markdown-to-gdocs`：Node.js, 自分のGoogle Cloud OAuthクライアント（詳細はSKILL.md参照）
- `transcribe-meeting`：google-genai, ffmpeg/ffprobe, Gemini APIキー（詳細はSKILL.md参照）
- `interest-profile`：Python3（`scripts/extract_interests.py`）
- `mcp-setup`・`format-prompt`・`meishi-rename`・`person-research`：追加依存なし（`person-research`はWebFetch/WebSearch・Agentツールを使用）
- `schedule-reply`：Node.js, Playwright, 自分のGoogle OAuth認証情報（詳細はSKILL.md参照）
- `email-to-calendar`：Gmail/Calendar MCPツール（環境に設定されたもの）。追加ソフトウェア依存なし

## その他のツール

Skill（自動発見）ではなく、個別にセットアップして使うツール。

| ツール | 内容 |
|---|---|
| [statusline](tools/statusline/README.md) | Claude Codeのターミナル下部に使用状況（コンテキスト・レート制限・作業フォルダ・アカウント）を表示 |
| [gmail-message-id-bookmarklet](tools/gmail-message-id-bookmarklet/README.md) | Gmailで開いているメールのMessage-IDをクリップボードにコピーするブックマークレット（Claudeにメールを一意に伝えるため） |
| [title-url-bookmarklet](tools/title-url-bookmarklet/README.md) | 現在開いているページの「タイトル + 改行 + URL」をクリップボードにコピーするブックマークレット |
| [copy-box-path-bookmarklet](tools/copy-box-path-bookmarklet/README.md) | Box.comのファイル/フォルダページで「Box内フォルダパス + URL」をコピーするブックマークレット（Box以外のページではTitle and URLと同じ動作にフォールバック） |
| [rtk](tools/rtk/README.md) | 2026-08-01に廃止。bash出力が静かに別内容へ置き換わり誤った集計を生む障害のため。2026-07-27〜08-01に導入した人は削除手順を参照 |

ブックマークレット3件は [GitHub Pages: bookmarklets](https://saito-la.github.io/claude-toolkit/) からドラッグ&ドロップでインストールできる（手動貼り付け手順は各ツールのREADME参照）。
