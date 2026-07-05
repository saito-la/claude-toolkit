---
name: markdown-to-gdocs
description: markdownやdocxをテンプレート体裁のGoogle Docsとして共有ドライブ・Google ドライブに作成したいとき（「Google Docsにアップして」「共有ドライブにドキュメント化して」等）に使うスキル。要事前セットアップ（自分のGoogle Cloud OAuthクライアント）。
---

# Markdown → Google Docs 変換・アップロード・体裁適用

ローカルの markdown を、テンプレート準拠の体裁を持つ Google Docs として Google ドライブ（共有ドライブ可）へ作成する3ステップパイプライン。[markdown-export](../markdown-export/SKILL.md) スキルの `md2docx.py` と対になる。

**このSKILL.mdが置かれているディレクトリを基準に、`scripts/upload-gdoc.mjs`・`scripts/format-gdoc.mjs` を実行すること。**

## パイプライン

```bash
cd <mdのあるフォルダ>
python3 <markdown-exportのディレクトリ>/scripts/md2docx.py foo.md -o foo.docx --template gothic --page-numbers
node <このSKILL.mdのディレクトリ>/scripts/upload-gdoc.mjs foo.docx <folderID> --account <account>   # → 新DocのURL/IDを表示
node <このSKILL.mdのディレクトリ>/scripts/format-gdoc.mjs <docID> --account <account> --preset formal-ja  # 体裁適用（在り物Docにその場で適用）
```

- `--account <name>` は認証情報ファイル `~/.config/gdrive-mcp/.gdrive-credentials-<name>-rw.json` を切り替える。省略時は `default`。
- 体裁の再調整は `format-gdoc.mjs <docID>` を再実行するだけ（再アップロード不要）。
- 内容を変えたら 1→3 を再実行して新Docを作る（旧Docは Drive API でゴミ箱へ）。
- `--preset` は `format-gdoc.mjs` 冒頭の `PRESETS` オブジェクトで定義。既定は `formal-ja`（余白1.5cm・全文メイリオ・行間0.85・タイトル中央/サブタイトル右詰め）。用途別プリセットを増やす場合はここに追記する。

## 初回セットアップ

自分のGoogle Cloudプロジェクトが必要：

1. Google Cloud Console で新規プロジェクトを作成し、OAuth同意画面・OAuthクライアント（デスクトップアプリ）を設定。
2. Google Drive API・Google Docs API を有効化。
3. ダウンロードしたクライアントJSONを `~/.config/gdrive-mcp/credentials.json` に配置。
4. `upload-gdoc.mjs` を初回実行するとブラウザが開くので、使いたいGoogleアカウントでログイン→「未確認アプリ」は「詳細」→「移動」→許可。認証情報は `~/.config/gdrive-mcp/.gdrive-credentials-<account>-rw.json` に保存される。
5. 本番公開済みOAuthに昇格しておくと以後は自動更新で再認証不要になる（任意）。

## 設計上の要点・ハマりどころ

- **フォントは最後に当てる**：名前付きスタイル（タイトル/見出し）を後から適用すると直接指定のフォントが消える。フォントは段落スタイル変更の後に全文へ適用する。
- **文書言語ロケールは Docs API で変更できない**：変換後の Doc が英語になるのは、docx の docDefaults 主言語が en-US（テンプレ既定）のため。`md2docx.py` が docDefaults の主言語を書き換えて解決（`--lang` 既定 ja-JP）。
- **見出しのブックマークは源流で抑止**：pandoc の見出しID（auto_identifiers）が docx の bookmark になり Google で「ブックマーク」印になる。Docs API では検出・削除できないため、`md2docx.py` の pandoc 呼び出しに `-f markdown-auto_identifiers` を付けて生成させない。
- **行間は 1.0 未満（0.85 等）も指定可**：`lineSpacing` は百分率。密度は「2ページ目の開始位置」で校正する。
