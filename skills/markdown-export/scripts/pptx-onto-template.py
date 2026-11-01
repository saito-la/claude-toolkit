#!/usr/bin/env python3
"""pptx-onto-template.py — deck2pptx が作った pptx の各スライドを、主催者の PPT テンプレートのレイアウトへ載せ替える。

学会・大会が「指定のスライドテンプレートを使うこと」と求めるとき、HTML で作ったデッキを deck2pptx で
pptx にし、このスクリプトでテンプレートのマスター・レイアウト・テーマの上に載せ直す。ロゴ・足元・背景の
飾りはテンプレートのレイアウトが持つので、HTML 側では飾りを消した版を deck2pptx に通しておく。
テンプレートが毎回変わっても、差し替えるのはテンプレートのファイルとレイアウト名の対応だけで済む。

やること:
  - テンプレートの見本スライドをすべて外す（マスター・レイアウト・テーマは残す）
  - 元の各スライドを、対応表で指定したレイアウトの新しいスライドにし、図形・画像・文字をそのまま写す
  - 対応表に title があれば、その文字の図形をレイアウトのタイトルのプレースホルダーへ移す
    （位置と書式は元の図形のまま。アウトラインと読み上げにタイトルが出る）。空のプレースホルダーは消す
  - 発表者ノートを写す
  - 対応表に video があれば、動画を指定の位置に埋め込む（ポスター画像を付けられる）
  - 対応表で hidden を true にしたスライドは、スライドショーで飛ばす（予備のスライド）

対応表（JSON の配列。元のスライドの順に1件ずつ）:
  [{"layout": "Title Slide"},
   {"layout": "1_Title Only", "title": "Disclosures"},
   {"layout": "1_Title Only", "title": "Demo",
    "video": {"file": "demo.mp4", "poster": "demo.png", "x": 120, "y": 206, "w": 1232, "h": 770}}]
  video の x・y・w・h は deck2pptx と同じ 1920px＝13.333in の座標（--px-per-in で変えられる）。
  file・poster は対応表のある場所からの相対パスでよい。

Usage:
  python3 pptx-onto-template.py src.pptx template.pptx --map slides.json -o out.pptx [--title "..."] [--author "..."]

依存: python-pptx（`python3 -m venv <dir> && <dir>/bin/pip install python-pptx` で入れた Python で実行する）
確認: 出力は office2pdf.py で PDF にして全ページを目視する。テンプレートの書体が端末に無いと、
      PowerPoint は代わりの書体で描く。deck2pptx で書体を明示しておけば文字の書体は変わらない。
"""
import argparse
import copy
import io
import json
import pathlib
import re
import sys

try:
    from pptx import Presentation
    from pptx.util import Emu
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
except ImportError:
    sys.exit("ERROR: python-pptx がありません。python-pptx を入れた venv の Python で実行してください。")

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
R_ATTRS = [f"{{{R}}}embed", f"{{{R}}}link", f"{{{R}}}id"]


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def text_of(el):
    return norm("".join(t.text or "" for t in el.iter(f"{{{A}}}t")))


def clear_slides(prs):
    lst = prs.slides._sldIdLst
    for sid in list(lst):
        prs.part.drop_rel(sid.rId)
        lst.remove(sid)
    # 外したスライドを指すセクションは PowerPoint の修復の原因になるので消す
    ext = prs.part._element.find(f"{{{P}}}extLst")
    if ext is not None:
        for e in list(ext):
            if any(c.tag.endswith("}sectionLst") for c in e):
                ext.remove(e)


def find_layout(prs, name):
    names = []
    for m in prs.slide_masters:
        for lay in m.slide_layouts:
            names.append(lay.name)
            if lay.name == name:
                return lay
    sys.exit(f"ERROR: レイアウト「{name}」がテンプレートにありません。あるのは: {', '.join(names)}")


def remap_rels(el, src_part, dst_part):
    """写した要素が指す関係（画像・リンク）を、行き先のスライドの関係に張り替える。"""
    for node in el.iter():
        for attr in R_ATTRS:
            rid = node.get(attr)
            if not rid or rid not in src_part.rels:
                continue
            rel = src_part.rels[rid]
            if rel.is_external:
                new = dst_part.relate_to(rel.target_ref, rel.reltype, is_external=True)
            elif rel.reltype == RT.IMAGE:
                _, new = dst_part.get_or_add_image_part(io.BytesIO(rel.target_part.blob))
            else:
                new = dst_part.relate_to(rel.target_part, rel.reltype)
            node.set(attr, new)


def copy_slide(src, dst):
    tree_src, tree_dst = src.shapes._spTree, dst.shapes._spTree
    for el in tree_src:
        if el.tag in (f"{{{P}}}nvGrpSpPr", f"{{{P}}}grpSpPr", f"{{{P}}}extLst"):
            continue
        new = copy.deepcopy(el)
        remap_rels(new, src.part, dst.part)
        tree_dst.append(new)


def move_title(dst, title):
    """title と同じ文字の図形を、タイトルのプレースホルダーへ移す。見つからなければ False。"""
    ph = dst.shapes.title
    if ph is None or not title:
        return False
    for sp in dst.shapes._spTree.iterchildren(f"{{{P}}}sp"):
        if sp.find(f".//{{{P}}}nvPr/{{{P}}}ph") is not None:
            continue
        if text_of(sp) != norm(title):
            continue
        ph_el = ph._element
        # 位置：元の図形の xfrm をプレースホルダーに持たせる
        src_xfrm = sp.find(f"{{{P}}}spPr/{{{A}}}xfrm")
        sppr = ph_el.find(f"{{{P}}}spPr")
        old = sppr.find(f"{{{A}}}xfrm")
        if old is not None:
            sppr.remove(old)
        sppr.insert(0, copy.deepcopy(src_xfrm))
        # 文字と書式：txBody ごと置き換える（書体・大きさ・色・行送りは元の図形のまま）
        old_tb = ph_el.find(f"{{{P}}}txBody")
        new_tb = copy.deepcopy(sp.find(f"{{{P}}}txBody"))
        ph_el.replace(old_tb, new_tb)
        sp.getparent().remove(sp)
        return True
    return False


def drop_empty_placeholders(dst):
    for shp in list(dst.placeholders):
        if not shp.has_text_frame or not norm(shp.text_frame.text):
            shp._element.getparent().remove(shp._element)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("template")
    ap.add_argument("--map", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--title")
    ap.add_argument("--author")
    ap.add_argument("--px-per-in", type=float, default=144.0)
    a = ap.parse_args()

    mp = pathlib.Path(a.map).resolve()
    entries = json.loads(mp.read_text(encoding="utf-8"))
    src = Presentation(a.src)
    prs = Presentation(a.template)
    if (src.slide_width, src.slide_height) != (prs.slide_width, prs.slide_height):
        print(f"WARNING: スライドの大きさが違います（元 {src.slide_width}x{src.slide_height}、"
              f"テンプレート {prs.slide_width}x{prs.slide_height}）。図形は元の座標のまま置きます。", file=sys.stderr)
    if len(entries) != len(src.slides):
        sys.exit(f"ERROR: 対応表は {len(entries)} 件、元のスライドは {len(src.slides)} 枚です。")
    clear_slides(prs)
    px = lambda v: Emu(int(round(v / a.px_per_in * 914400)))

    for i, (s, e) in enumerate(zip(src.slides, entries), 1):
        dst = prs.slides.add_slide(find_layout(prs, e["layout"]))
        copy_slide(s, dst)
        titled = move_title(dst, e.get("title"))
        drop_empty_placeholders(dst)
        if s.has_notes_slide:
            notes = s.notes_slide.notes_text_frame.text
            if notes.strip():
                dst.notes_slide.notes_text_frame.text = notes
        v = e.get("video")
        if v:
            f = (mp.parent / v["file"]).resolve()
            if not f.exists():
                sys.exit(f"ERROR: 動画がありません: {f}")
            poster = (mp.parent / v["poster"]).resolve() if v.get("poster") else None
            dst.shapes.add_movie(str(f), px(v["x"]), px(v["y"]), px(v["w"]), px(v["h"]),
                                 poster_frame_image=str(poster) if poster else None, mime_type="video/mp4")
        if e.get("hidden"):
            dst._element.set("show", "0")
        flag = "title" if titled else ("no-title" if e.get("title") else "-")
        print(f"slide {i}: {e['layout']} [{flag}]{' +video' if v else ''}{' hidden' if e.get('hidden') else ''}", file=sys.stderr)

    if a.title:
        prs.core_properties.title = a.title
    if a.author:
        prs.core_properties.author = a.author
        prs.core_properties.last_modified_by = a.author
    prs.save(a.out)
    print(a.out)


if __name__ == "__main__":
    main()
