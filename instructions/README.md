# instructions

`~/.claude/CLAUDE.md` に `@import` で取り込むグローバル指示の断片。インストーラが `~/.claude/instructions/` へ配置する。**配置しただけでは読まれない**——取り込むかどうかは各自の判断で、`~/.claude/CLAUDE.md` に次のような行を足すと有効になる。

```markdown
@instructions/language.md
@instructions/tools.md
@instructions/documents.md
@instructions/principles.md
```

## 断片に相対 @import を書かない

**この規則を破ると、書いた import が黙って読まれない。**

Claude Code の相対 `@import` は、読み込み元ファイルの実体が許可ルート（設定ディレクトリ）の外にあると解決できない。ここの断片は `~/.claude/instructions/` に symlink またはコピーで置かれ、symlink の場合は実体が別リポジトリ＝許可ルート外になる。

したがって、**`~/.claude/CLAUDE.md` が全断片をフラットに列挙するルーターになり、断片自身は import を持たない葉にする。** 断片を増やしたら、ルーター側に1行足す。

## ローカル補足

個人・組織に固有の内容はここへ入れず、`~/.claude/instructions/` に自分のファイルを置いてルーターから import する。共有される断片と混ぜない。
