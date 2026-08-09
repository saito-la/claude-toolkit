## 文書の変換・出力

- markdown → docx / PDF は **`markdown-export` スキル経由**。pandoc を直接呼ばない。
- md の印刷を頼まれたら生 md を渡さず、PDF 化してから印刷する。
- **相手が編集する可能性がある添付は docx で渡す**（PDF にしない）。事務方・共同編集者へ回す起案文書・仕様書・証明書・契約書案などが該当する。読ませるだけの資料は PDF でよい。元が Google Docs なら Drive API の docx エクスポートで体裁ごと渡す。
- md の Google Docs 化は **`markdown-to-gdocs` スキルの3段パイプライン**。**docx を Drive へ直アップロード（`files.create`/`files.update` media）すると体裁が崩れる**（禁止）。内容変更時は3段を再実行して新Docを作り、旧Docは Drive API でゴミ箱へ。

## 会話の記録・保存

まとまった知見・決定・手順が出たタイミングで `.md` ファイル保存を提案してよい。

- プロジェクト固有 → そのプロジェクトの `docs/` 配下／環境・設定の知見 → 自分の環境設定ドキュメント（無ければ適宜作成）
- ファイル名は英語ケバブケース（例: `mcp-setup-guide.md`）
- 外部資料（Google Doc等）を読んだら内容を該当プロジェクトの**永続 md に保存**する。memory は索引に過ぎず、全文を残さない

## 会議録音の成果物を git に入れる範囲

`transcribe-meeting` の成果物のうち **git 管理するのは summary だけ**。transcript（逐語）・verbatim（ケバ取り）・`*_usage.json`・音声・チャンクはリポジトリに含めず、`attachments/<日付>/` 等のローカル置き場に留めて `.gitignore` で除外する。

理由：逐語には人物の状態・評価に関する生の発言（健康状態・メンタル・力量評価など）がそのまま残る。一度コミットすると private リポジトリでも履歴から消せず、消すには履歴の書き換えと force push が要る。summary は同じ事実を扱う場合も、要約の過程で文脈と表現が整理される。

除外パターンの例（プロジェクトの `.gitignore`）：

```
*_transcript.txt
*_transcript.txt.bak
*_verbatim.txt
*_usage.json
*.m4a.txt
```

既存プロジェクトでこの規約より前にコミットされた逐語ファイルを見つけたら、`git rm --cached` で追跡から外すことを提案する（過去のコミットには残る旨も併せて伝える）。
