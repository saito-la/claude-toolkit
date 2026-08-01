## 文書の変換・出力

- markdown → docx / PDF は **`markdown-export` スキル経由**。pandoc を直接呼ばない。
- md の印刷を頼まれたら生 md を渡さず、PDF 化してから印刷する。
- md の Google Docs 化は **`markdown-to-gdocs` スキルの3段パイプライン**。**docx を Drive へ直アップロード（`files.create`/`files.update` media）すると体裁が崩れる**（禁止）。内容変更時は3段を再実行して新Docを作り、旧Docは Drive API でゴミ箱へ。

## 会話の記録・保存

まとまった知見・決定・手順が出たタイミングで `.md` ファイル保存を提案してよい。

- プロジェクト固有 → そのプロジェクトの `docs/` 配下／環境・設定の知見 → 自分の環境設定ドキュメント（無ければ適宜作成）
- ファイル名は英語ケバブケース（例: `mcp-setup-guide.md`）
- 外部資料（Google Doc等）を読んだら内容を該当プロジェクトの**永続 md に保存**する。memory は索引に過ぎず、全文を残さない
