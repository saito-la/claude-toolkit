#!/usr/bin/env python3
"""Markdown を Apple Notes のノートにする。手順と文字サイズの根拠は guides/NOTES-SEND.md。

使い方:
  md-to-notes.py FILE.md [--split-h2] [--prefix 文字列] [--folder フォルダ名] [--scale 倍率] [--send | --diff]

- 既定はプレビュー。ノートごとのプレーンテキストを標準出力に出すだけで、送らない。
- --send で Notes.app にノートを作る。作成先に同じ名前のノートがあれば、新しく作らずに本文を置き換える。作成後に読み戻し、文字サイズの反映を確かめて ID を出す。
- --diff は Notes 側の現在の本文と md を比べ、Notes で直された行を差分で出す（md へ取り込む前の確認用。送らない）。
- --split-h2 は H2 の節ごとに1ノートにする（会議ごとに開く用途など）。無ければファイル全体で1ノート。
- --prefix はノート名の頭に付ける文字列。--folder は作成先のフォルダ（無ければ作る）。
- --scale は文字サイズの倍率（既定 1.0 で本文19px・見出し23px）。声に出して読み上げる台本は 1.5（本文28px・見出し34px）にする（2026-10-08、19px では読み上げにくかった）。
- H1 と冒頭の「作成日：」「改訂日：」行は落とす。リンクは文字だけ、太字・コードの記号は外す。
- コードブロック（樹形図など）は字下げを保って等幅（Menlo、--scale に関わらず 15px）で出す。1 行だけの <img src="..."> は画像を取得して埋め込む（長辺 240px。取れなければ「［写真：alt］」の文字にする）。
- 節ごとに分けたときは、Notes の一覧（編集日順）で先頭の節が上に来るよう、末尾の節から作る。
"""
import argparse, difflib, html, os, re, subprocess, sys, tempfile

BODY_PX, HEAD_PX = 19, 23  # 2026-10-02 の実測では Notes が指定の px をそのまま保つ。手順書の承認済みサイズ（本文19px・見出し23px）を直接指定する


def inline(s):
    s = re.sub(r'\[([^\]]+)\]\(<?[^)>]*>?\)', r'\1', s)
    s = re.sub(r'<(https?://[^>]+)>', r'\1', s)
    s = s.replace('**', '').replace('`', '')
    return s.strip()


def parse(md, split):
    lines = md.splitlines()
    notes, cur = [], None
    for line in lines:
        if line.startswith('# ') or re.match(r'^(作成日|改訂日)：', line):
            continue
        if split and line.startswith('## '):
            cur = {'title': inline(line[3:]), 'rows': []}
            notes.append(cur)
            continue
        if cur is None:
            cur = {'title': None, 'rows': []}
            notes.append(cur)
        cur['rows'].append(line)
    return [n for n in notes if any(r.strip() for r in n['rows']) or n['title']]


def rows_to_blocks(rows):
    """(種類, 文字列) の列にする。種類は head・item・text・blank・code・img。"""
    out, in_code = [], False
    for r in rows:
        if r.lstrip().startswith('```'):  # コードブロック（樹形図など）は字下げを保って等幅で出す（2026-10-08、字下げが落ちて樹形図が崩れた）
            in_code = not in_code
            continue
        if in_code:
            out.append(('code', r.rstrip()))
            continue
        m = re.match(r'^\s*<img\s[^>]*src="([^"]+)"[^>]*>\s*$', r)
        if m:  # 顔写真などの <img> は画像として埋め込む（2026-10-08、タグが文字のまま出ていた）
            alt = re.search(r'alt="([^"]*)"', r)
            out.append(('img', m.group(1) + '\t' + (alt.group(1) if alt else '')))
            continue
        if not r.strip():
            if out and out[-1][0] != 'blank':
                out.append(('blank', ''))
            continue
        if re.match(r'^#{2,6} ', r):  # H4 以下も見出しにする（2026-10-07、#### が本文に残った）
            out.append(('head', inline(r.lstrip('#'))))
        elif re.match(r'^\s*- ', r):
            out.append(('item', '・' + inline(re.sub(r'^\s*- ', '', r))))
        elif re.match(r'^\d+\. ', r):
            out.append(('item', inline(r)))
        elif len(r.strip()) <= 10 and not re.search(r'[。、.,:：（(]', r):
            out.append(('head', inline(r)))  # 「背景」「聞くこと」のような1語の小見出し
        else:
            out.append(('text', inline(r)))
    while out and out[-1][0] == 'blank':
        out.pop()
    return out


def to_plain(title, blocks):
    lines = [title, '']
    for k, t in blocks:
        if k == 'head':
            lines.append('■' + t)
        elif k == 'img':
            lines.append('［写真：' + (t.split('\t')[1] or t.split('\t')[0]) + '］')
        else:
            lines.append(t)
    return '\n'.join(lines)


def img_data_uri(url):
    """画像を取得し、長辺 240px の JPEG にして data URI で返す。取れなければ None。Notes は data URI の画像を添付として取り込む。"""
    import base64, urllib.request
    try:
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, 'src'), os.path.join(d, 'dst.jpg')
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=20) as r, open(src, 'wb') as f:
                f.write(r.read())
            subprocess.run(['sips', '-s', 'format', 'jpeg', '-Z', '240', src, '--out', dst],
                           check=True, capture_output=True)
            with open(dst, 'rb') as f:
                return 'data:image/jpeg;base64,' + base64.b64encode(f.read()).decode()
    except Exception:
        return None


def to_html(title, blocks, body_px=BODY_PX, head_px=HEAD_PX):
    e = html.escape
    parts = [f'<div style="font-size:{body_px}px; line-height:1.55;">',
             f'<div style="font-size:{head_px}px;"><b>{e(title)}</b></div>', '<br>']
    for k, t in blocks:
        if k == 'blank':
            parts.append('<br>')
        elif k == 'head':
            parts.append(f'<b>■{e(t)}</b><br>')
        elif k == 'code':
            code_px = round(BODY_PX * 0.8)  # 樹形図は幅が要るので、--scale に関わらず小さく保つ
            parts.append(f'<span style="font-family:Menlo; font-size:{code_px}px;">'
                         f'{e(t).replace(" ", "&nbsp;")}</span><br>')
        elif k == 'img':
            url, alt = t.split('\t')
            uri = img_data_uri(url)
            parts.append(f'<img src="{uri}" width="120"><br>' if uri else f'{e("［写真：" + (alt or url) + "］")}<br>')
        else:
            parts.append(f'{e(t)}<br>')
    parts.append('</div>')
    return '\n'.join(parts)


def osa(script):
    r = subprocess.run(['osascript', '-'], input=script, capture_output=True, text=True)
    if r.returncode:
        sys.exit('osascript failed: ' + r.stderr.strip())
    return r.stdout.strip()


def send(title, body_html, folder, body_px=BODY_PX):
    with tempfile.NamedTemporaryFile('w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(body_html)
        path = f.name
    target = 'default account'
    if folder:
        q = folder.replace('"', '')
        osa(f'tell application "Notes"\nif not (exists folder "{q}") then make new folder with properties {{name:"{q}"}}\nend tell')
        target = f'folder "{q}"'
    t = title.replace('"', '')
    nid = osa(f'set theBody to read (POSIX file "{path}") as «class utf8»\n'
              f'tell application "Notes"\nset hits to notes of {target} whose name is "{t}"\n'
              f'if (count of hits) > 0 then\nset n to item 1 of hits\nset body of n to theBody\nelse\n'
              f'set n to make new note at {target} with properties {{body:theBody}}\nend if\nreturn id of n\nend tell')
    os.unlink(path)
    body = osa(f'tell application "Notes" to return body of note id "{nid}"')
    ok = f'font-size: {body_px}px' in body or f'font-size:{body_px}px' in body
    print(f'CREATED\t{title}\t{nid}\tsize-ok={ok}')


def notes_lines(title, folder):
    """Notes の本文を行のリストにする。HTML の div と br を改行とみなす。"""
    t = title.replace('"', '')
    target = f'folder "{folder}"' if folder else 'default account'
    body = osa(f'tell application "Notes"\nset hits to notes of {target} whose name is "{t}"\n'
               f'if (count of hits) = 0 then return ""\nreturn body of item 1 of hits\nend tell')
    body = re.sub(r'(?i)<br\s*/?>|</div>', '\n', body)
    body = html.unescape(re.sub(r'<[^>]+>', '', body)).replace('\xa0', ' ')
    return [l.strip() for l in body.split('\n') if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file')
    ap.add_argument('--split-h2', action='store_true')
    ap.add_argument('--prefix', default='')
    ap.add_argument('--folder', default='')
    ap.add_argument('--send', action='store_true')
    ap.add_argument('--diff', action='store_true')
    ap.add_argument('--scale', type=float, default=1.0)
    a = ap.parse_args()
    body_px, head_px = round(BODY_PX * a.scale), round(HEAD_PX * a.scale)
    md = open(a.file, encoding='utf-8').read()
    h1 = next((inline(l[2:]) for l in md.splitlines() if l.startswith('# ')), os.path.basename(a.file))
    notes = parse(md, a.split_h2)
    built = []
    for n in notes:
        title = (a.prefix + ' ' if a.prefix else '') + (n['title'] or h1)
        blocks = rows_to_blocks(n['rows'])
        built.append((title, blocks))
    if a.diff:
        for title, blocks in built:
            md_lines = [l.strip() for l in to_plain(title, blocks).splitlines() if l.strip() and not l.startswith('［写真：')]
            d = [x for x in difflib.unified_diff(md_lines, notes_lines(title, a.folder), 'md', 'notes', lineterm='', n=0)
                 if not x.startswith(('---', '+++'))]
            print(f'== {title}: {sum(1 for x in d if x[:1] in "+-")} 行の差')
            for x in d:
                print(x)
        return
    if not a.send:
        for title, blocks in built:
            print(to_plain(title, blocks))
            print('\n' + '=' * 40 + '\n')
        return
    for title, blocks in reversed(built):
        send(title, to_html(title, blocks, body_px, head_px), a.folder, body_px)


if __name__ == '__main__':
    main()
