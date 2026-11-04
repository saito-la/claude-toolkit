#!/usr/bin/env python3
"""pj-recommend — 「やりたいこと」(自然文) から起動すべきプロジェクトを推薦する補助スクリプト。

pj() (dotfiles/.zshrc.claude) と pj-router.py から呼ばれる索引・カタログの単一情報源。

仕組み:
  1. ~/Projects 配下の全 CLAUDE.md から「目的」「切り分け/使い分け」節、
     任意の `<!-- pj-tags: ... -->`、および frontmatter（org/unit/domain/aliases等。
     無ければ後方互換で空扱い）を抽出し、~/.cache/pj/index.json にキャッシュ。
     CLAUDE.md の mtime 集合が変わったら自動で再生成（drift なし・二重管理なし）。
  2. catalog モードで圧縮カタログ（1行1プロジェクト）を出力する。判断は呼び出し側
     （/pj なら Claude 本体、シェルなら fzf）が行う。haiku による前段ランキングは
     2026-07-26 に廃止（LLM が LLM を呼ぶ層の除去）。
  3. list モードは正規プロジェクトの relpath のみを出す（列挙の単一情報源）。

Usage:
  pj-recommend.py index [--rebuild]        # インデックスを生成/再生成し、目的が空の項目を警告
  pj-recommend.py catalog                  # 圧縮カタログを標準出力（判断は呼び出し側）
  pj-recommend.py list                     # 正規プロジェクトの relpath のみ
  pj-recommend.py --suggest-frontmatter [relpath-substring]
                                            # frontmatter未投入プロジェクトへの投入案を提示（提案のみ・書込みなし）
"""
import hashlib
import json
import os
import pathlib
import re
import sys

# Windows では標準出力が cp932・CRLF になり、呼び出す bash が日本語と行末を読み違える。
# UTF-8・LF に揃える（pj-platform.sh の冒頭）
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", newline="\n")

HOME = pathlib.Path.home()
PROJECTS = HOME / "Projects"
CACHE_DIR = HOME / ".cache" / "pj"
CACHE = CACHE_DIR / "index.json"

MAXDEPTH = 5            # find -maxdepth 5 相当（PROJECTS からのディレクトリ深さ）
PRUNE_DIRS = {"node_modules", "__pycache__"}

PURPOSE_HEADS = ("目的", "このフォルダ", "このディレクトリ", "概要", "purpose", "about")
BOUNDARY_HEADS = ("切り分け", "使い分け", "scope", "何を置", "境界")
PURPOSE_LIMIT = 220
BOUNDARY_LIMIT = 300

# frontmatter は CLAUDE.md 先頭の YAML風ブロックのみ対象（key: value と [list] のみ。PyYAML不採用）
FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*\n", re.DOTALL)
FRONTMATTER_LIST_FIELDS = ("domain", "aliases", "related", "unit")
FRONTMATTER_BOOL_FIELDS = ("confidential",)
JP_TOKEN_RE = re.compile(r"[0-9A-Za-z]+|[一-鿿぀-ヿ]{2,}")
MINE_STOPWORDS = {
    "readme", "claude", "docs", "doc", "reference", "references", "action", "items",
    "work", "log", "logs", "draft", "template", "templates", "index", "raw", "ds", "store",
}


def find_claude_mds():
    """~/Projects 配下の CLAUDE.md を列挙（隠しディレクトリ・node_modules等は除外）。

    同じディレクトリに .pjignore があれば除外する。テンプレートの配布物や編集用
    ワークスペースのように、CLAUDE.md はあるが「Claude Code で作業を起こす場所」
    ではないディレクトリを一覧から外すためのマーカー（zsh 版 pj と同じ規約。
    dotfiles/.zshrc.claude 参照）。
    """
    out = []
    for root, dirs, files in os.walk(PROJECTS):
        rel = pathlib.Path(root).relative_to(PROJECTS)
        depth = 0 if str(rel) == "." else len(rel.parts)
        # 隠しディレクトリ(.git/.claude 等)とビルド成果物を枝刈り
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in PRUNE_DIRS]
        if depth >= MAXDEPTH:
            dirs[:] = []
        if "CLAUDE.md" in files and ".pjignore" not in files:
            out.append(pathlib.Path(root) / "CLAUDE.md")
    return out


def parse_sections(text):
    """Markdown を (見出し, 本文行リスト) のリストに分解。先頭見出しをタイトルとする。"""
    sections = []
    cur_head, cur_body = None, []
    for ln in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            if cur_head is not None:
                sections.append((cur_head, cur_body))
            cur_head, cur_body = m.group(2).strip(), []
        elif cur_head is not None:
            cur_body.append(ln)
    if cur_head is not None:
        sections.append((cur_head, cur_body))
    return sections


def clean(body_lines, limit):
    txt = " ".join(l.strip() for l in body_lines)
    txt = re.sub(r"<!--.*?-->", " ", txt, flags=re.DOTALL)  # HTMLコメント除去
    txt = re.sub(r"[`*#>|]", " ", txt)                       # md記号・表パイプ除去
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:limit]


def extract(sections):
    purpose, boundary = "", ""
    for head, body in sections:
        h = head.lower()
        if not purpose and any(k in head or k in h for k in PURPOSE_HEADS):
            purpose = clean(body, PURPOSE_LIMIT)
        if not boundary and any(k in head or k in h for k in BOUNDARY_HEADS):
            boundary = clean(body, BOUNDARY_LIMIT)
    if not purpose:  # フォールバック: 最初の非空セクション本文
        for _, body in sections:
            c = clean(body, PURPOSE_LIMIT)
            if c:
                purpose = c
                break
    return purpose, boundary


def extract_tags(text):
    m = re.search(r"<!--\s*pj-tags:\s*(.*?)-->", text, flags=re.DOTALL)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def parse_frontmatter(text):
    """CLAUDE.md 先頭の `---`...`---` ブロックを key: value / key: [a, b] のみ解釈する軽量パーサ。
    無ければ空dict（frontmatter無しプロジェクトは従来通り purpose/boundary/tags のみで索引される）。
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    data = {}
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key in FRONTMATTER_LIST_FIELDS:
            inner = val.strip("[]").strip()
            data[key] = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()] if inner else []
        elif key in FRONTMATTER_BOOL_FIELDS:
            data[key] = val.lower() == "true"
        else:
            data[key] = val.strip("'\"")
    return data


def fingerprint(paths):
    items = sorted((str(p), int(p.stat().st_mtime)) for p in paths)
    return hashlib.md5(json.dumps(items).encode()).hexdigest()


def build_entries(paths):
    entries = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = p.parent.relative_to(PROJECTS).as_posix()
        purpose, boundary = extract(parse_sections(text))
        fm = parse_frontmatter(text)
        entries.append({
            "path": rel,
            "purpose": purpose,
            "boundary": boundary,
            "tags": extract_tags(text),
            "org": fm.get("org", ""),
            "unit": fm.get("unit", []),
            "domain": fm.get("domain", []),
            "aliases": fm.get("aliases", []),
            "lifecycle": fm.get("lifecycle", ""),
            "status": fm.get("status", ""),
            "confidential": fm.get("confidential", False),
            "related": fm.get("related", []),
        })
    entries.sort(key=lambda e: e["path"])
    return entries


def load_or_build(rebuild=False):
    paths = find_claude_mds()
    fp = fingerprint(paths)
    if not rebuild and CACHE.exists():
        try:
            data = json.loads(CACHE.read_text(encoding="utf-8"))
            if data.get("fingerprint") == fp:
                return data["entries"]
        except (OSError, ValueError):
            pass
    entries = build_entries(paths)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"fingerprint": fp, "entries": entries}, ensure_ascii=False), encoding="utf-8")
    return entries


def catalog_lines(entries):
    """呼び出し側（Claude／fzf）が読む圧縮カタログを 1行1プロジェクトで返す。

    haiku サブプロセスによる前段ランキングは廃止した（2026-07-26）。索引全体は
    約10.5k est.トークンで本体コンテキストに載せられなかったが、この圧縮版は
    約3.5k で、曖昧なとき1回読むだけなら安い。LLM が LLM を呼ぶ層が消える。

    `unit`（所管部門）は索引には載せるがカタログには出さない。8部門名が23フォルダに
    重複して現れるだけで弁別力が無く（同じ部門名で複数候補が並ぶ）、カタログは全件が
    毎回コンテキストに載るため肥大が直接コストになる。unit の用途はルーティングでなく
    改組耐性（組織帰属の機械可読な記録）で、index.json 経由で監査・crosswalk が読む。
    """
    out = []
    for e in entries:
        seg = e["path"]
        if e.get("aliases"):
            seg += " | 別名: " + "、".join(e["aliases"])
        if e.get("purpose"):
            seg += " | " + e["purpose"][:80]
        if e.get("boundary"):
            seg += " | 切り分け: " + e["boundary"][:60]
        out.append(seg)
    return out


def cmd_catalog():
    for line in catalog_lines(load_or_build()):
        print(line)
    return 0


def cmd_list():
    """正規プロジェクトの relpath だけを出す（.pjignore 済み）。

    zsh の pj() が CLAUDE.md を独自に find していたのを置き換えるための単一情報源。
    列挙が2箇所にあると .pjignore の対応漏れが起きる（2026-07-26 に実際に起きた）。
    """
    for e in load_or_build():
        print(e["path"])
    return 0


def guess_org(rel_path):
    top = rel_path.split("/", 1)[0]
    return top


def mine_aliases(proj_dir, known_text_lower, limit=15):
    """サブフォルダ名・ファイル名（深さ2まで）の日本語/英数トークンのうち、
    既存の purpose/boundary/tags に出てこないものを別名候補として掘り起こす。
    """
    tokens = []
    seen = set()
    for root, dirs, files in os.walk(proj_dir):
        rel_root = pathlib.Path(root).relative_to(proj_dir)
        depth = 0 if str(rel_root) == "." else len(rel_root.parts)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in PRUNE_DIRS]
        if depth >= 2:
            dirs[:] = []
        if depth > 2:
            continue
        for name in list(dirs) + files:
            if name.startswith("."):
                continue
            stem = pathlib.Path(name).stem
            for tok in JP_TOKEN_RE.findall(stem):
                key = tok.lower()
                if key in known_text_lower or key in seen or key in MINE_STOPWORDS:
                    continue
                if key.isdigit():  # 日付・連番等
                    continue
                seen.add(key)
                tokens.append(tok)
    return tokens[:limit]


def cmd_suggest_frontmatter(filter_substr=""):
    paths = find_claude_mds()
    printed = 0
    for p in paths:
        text = p.read_text(encoding="utf-8", errors="replace")
        # 「frontmatter ブロックの有無」では判定しない。2026-07-26 に CRPC 配下11フォルダへ
        # `unit` だけの最小 frontmatter を投入しており、ブロック有無で切ると org/domain/aliases の
        # backfill 対象から永久に外れる。ルーティングの中核キー `domain` の有無を投入済みの指標とする。
        fm = parse_frontmatter(text)
        if fm.get("domain"):
            continue  # 既に routing 用 frontmatter 投入済み → 対象外
        rel = p.parent.relative_to(PROJECTS).as_posix()
        if filter_substr and filter_substr.lower() not in rel.lower():
            continue
        purpose, boundary = extract(parse_sections(text))
        known = (purpose + " " + boundary + " " + extract_tags(text)).lower()
        aliases = mine_aliases(p.parent, known)
        printed += 1
        print(f"## {rel}")
        print("```yaml")
        print("---")
        print(f"org: {guess_org(rel) or '???'}")
        print("domain: []  # 要人手入力")
        print(f"aliases: [{', '.join(aliases)}]" if aliases else "aliases: []  # 掘り起こし候補なし")
        print("lifecycle: standing")
        print("status: active")
        print("confidential: false  # 機密なら true に変更")
        print("related: []")
        print("---")
        print("```")
        print()
    if not printed:
        print("frontmatter未投入かつ条件に合致するプロジェクトはありません。")
    return 0


def cmd_index(rebuild):
    entries = load_or_build(rebuild=rebuild)
    print(f"インデックス: {len(entries)} プロジェクト  →  {CACHE}")
    weak = [e["path"] for e in entries if not e["purpose"]]
    if weak:
        print(f"\n目的が空/未記載 ({len(weak)}件) — 推薦精度向上のため各 CLAUDE.md の「## 目的」を埋めてください:")
        for w in weak:
            print(f"  - {w}")
    return 0


def main(argv):
    if len(argv) < 2 or argv[1] not in ("index", "catalog", "list", "--suggest-frontmatter"):
        sys.stderr.write(__doc__)
        return 64
    if argv[1] == "index":
        return cmd_index(rebuild="--rebuild" in argv[2:])
    if argv[1] == "catalog":
        return cmd_catalog()
    if argv[1] == "list":
        return cmd_list()
    return cmd_suggest_frontmatter(argv[2] if len(argv) > 2 else "")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
