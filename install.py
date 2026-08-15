#!/usr/bin/env python3
"""claude-toolkit を ~/.claude/ へ配置する。

**配置ロジックはここが唯一の実装。** 配布リポジトリ（saito-la/claude・crpc-tools）も
本人環境（ai-environment/link-dotfiles.sh）も、自前で置き方を書かず本スクリプトを呼ぶ。

かつては配布先ごとに置き方を書き直していた（bash・PowerShell・Python で4実装）。
同じことをする4つのコードは必ずズレる——実際、statusLine を「既にあれば触らない」
ように直したのは2つだけで、残り1つは無条件に上書きし続け、本人環境の設定を2度壊した
（2026-08-01）。配布先の数（3）と実装の数が一致する必然性は無い。

## 2回目以降の実行（更新）

**冪等かつ、消えたものが消える。** 何を置いたかを `~/.claude/.wired-by` に記録し、
次回そこに無いものを撤去する。旧4実装は置き直すだけだったので、上流で廃止した Skill・
規約が端末に残り続け、消したはずのものが読まれていた。

  追加  上流に増えた → 置かれる
  変更  symlink なら git pull で即反映（copy は本スクリプトの再実行が要る）
  削除  上流から消えた → **前回の記録と突き合わせて撤去する**
  移行  symlink ⇄ copy の切り替え、別の配布元からの乗り換えも扱う

配置するもの:
  skills/*/                      → ~/.claude/skills/<name>
  tools/statusline/statusline.py → ~/.claude/statusline.py
  conventions/*.md               → ~/.claude/conventions/
  guides/*.md                    → ~/.claude/
  instructions/*.md              → ~/.claude/instructions/
  settings.json の statusLine     → 未設定なら追記／本スクリプトが書いた値なら更新

`instructions/` は置くだけでは読まれない。~/.claude/CLAUDE.md に `@instructions/<名前>`
を足して初めて効く（CLAUDE.md が無ければ作る。あれば書き換えず、足りない行を表示する）。

使い方:
  python3 install.py [オプション]
    --root <dir>    配布元。既定は本スクリプトのあるディレクトリ
    --mode symlink|copy
                    既定は POSIX=symlink / Windows=copy（Windows の symlink は権限が要る）
    --label <name>  呼び出し元の名前。~/.claude/.wired-by に診断用として残す
    --no-settings   settings.json に触らない（呼び出し側が settings.json を管理する場合）
    --force         別の配布元が配線済みでも上書きする
    --dry-run       何が追加・更新・撤去されるかだけ表示する
    --quiet
"""

import argparse
import json
import os
import platform
import shutil
import sys
from datetime import datetime
from pathlib import Path

CLAUDE = Path.home() / ".claude"
MARKER = CLAUDE / ".wired-by"

# <正本ディレクトリ名> → <~/.claude 内の配置先。空文字は ~/.claude 直下>
MD_DIRS = [("conventions", "conventions"), ("guides", ""), ("instructions", "instructions")]


class Plan:
    """今回置くもの（相対パス）と、実際に行った操作を集める。"""

    def __init__(self):
        self.placed = []      # ~/.claude からの相対パス
        self.added = []
        self.removed = []
        self.failed = []

    def record(self, rel, existed):
        self.placed.append(rel)
        if not existed:
            self.added.append(rel)


def log(msg, quiet=False):
    if not quiet:
        print(msg)


# --- 配線元マーカー -------------------------------------------------------
#
# 1台のマシンに配布元が複数あると（本人機には正本 clone と、配布リポジトリが抱える
# vendor submodule が同居する）、後から走ったインストーラが勝ち、参照先が黙って
# スナップショット側へ倒れる。「配線し直すのを人間が覚えている」という不変条件は
# 実際に2度破れたので、機械が持ち主を宣言して守る形にした。
#
# 同じファイルに「前回何を置いたか」も持たせる。撤去の判断に要る。

def read_marker() -> dict:
    if not MARKER.is_file():
        return {}
    try:
        return json.loads(MARKER.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_marker(root, label, mode, placed):
    MARKER.write_text(
        json.dumps(
            {
                "_comment": "claude-toolkit の配線元と配置物。install.py が管理する（手で編集しない）",
                "root": str(root),
                "label": label,
                "mode": mode,
                "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
                "placed": sorted(placed),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def check_owner(root, label, force, quiet) -> bool:
    prev = read_marker()
    prev_root = prev.get("root")
    if not prev_root or prev_root == str(root):
        return True
    if force:
        log(f"⚠️  配線元を {prev_root} から {root} へ切り替えます（--force）", quiet)
        return True
    print(
        f"""
このマシンの ~/.claude/ は既に別の配布元から配線されています。上書きを中止しました。

  現在の配線元: {prev_root}
    （{prev.get('label', '不明')} が {prev.get('updated', '不明')} に配置）
  今回の配布元: {root}
    （{label}）

中身は同じ claude-toolkit でも、参照先が変わると更新の届き方が変わります。
正本の clone を指していたものが配布用スナップショット（submodule）に切り替わると、
正本を編集しても submodule を bump するまで反映されなくなります。

意図した切り替えなら --force を付けてください。
インストーラの動作確認が目的なら、ホームを分けたサンドボックスで実行してください:

  HOME=$(mktemp -d) python3 {Path(__file__).name}
""".rstrip()
    )
    return False


# --- 配置 -----------------------------------------------------------------

def _clear(dest: Path):
    """置き換え前に退ける。symlink は unlink（rmtree は symlink に例外を投げる）。"""
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.is_dir():
        shutil.rmtree(dest)


def _place(src: Path, dest: Path, mode: str) -> bool:
    """配置する。既に望みの状態なら何もしない（戻り値=既存だったか）。"""
    if mode == "symlink" and dest.is_symlink() and Path(os.readlink(dest)) == src:
        return True
    existed = dest.exists() or dest.is_symlink()
    _clear(dest)
    if mode == "symlink":
        dest.symlink_to(src, target_is_directory=src.is_dir())
    elif src.is_dir():
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    else:
        shutil.copy2(src, dest)
    return existed


def place_skills(root: Path, mode: str, plan: Plan, dry: bool, quiet: bool):
    src_dir = root / "skills"
    if not src_dir.is_dir():
        return
    dest_dir = CLAUDE / "skills"
    if not dry:
        dest_dir.mkdir(parents=True, exist_ok=True)
    for skill in sorted(src_dir.iterdir()):
        if not (skill / "SKILL.md").is_file():
            continue
        rel = f"skills/{skill.name}"
        if dry:
            plan.record(rel, (CLAUDE / rel).exists())
            continue
        try:
            plan.record(rel, _place(skill, dest_dir / skill.name, mode))
        except OSError as e:
            plan.failed.append(rel)
            log(f"⚠️  {skill.name} の配置に失敗しました（スキップして続行）: {e}", quiet)


def place_md_dirs(root: Path, mode: str, plan: Plan, dry: bool, quiet: bool):
    for name, sub in MD_DIRS:
        src_dir = root / name
        if not src_dir.is_dir():
            continue
        dest_dir = CLAUDE / sub if sub else CLAUDE
        if not dry:
            dest_dir.mkdir(parents=True, exist_ok=True)
        for md in sorted(src_dir.glob("*.md")):
            if md.name == "README.md":  # 説明書きなので配置しない
                continue
            rel = f"{sub}/{md.name}" if sub else md.name
            if dry:
                plan.record(rel, (CLAUDE / rel).exists())
                continue
            try:
                plan.record(rel, _place(md, dest_dir / md.name, mode))
            except OSError as e:
                plan.failed.append(rel)
                log(f"⚠️  {md.name} の配置に失敗しました（スキップして続行）: {e}", quiet)


def place_statusline(root: Path, mode: str, plan: Plan, dry: bool, quiet: bool) -> bool:
    src = root / "tools" / "statusline" / "statusline.py"
    if not src.is_file():
        return False
    dest = CLAUDE / "statusline.py"
    if dry:
        plan.record("statusline.py", dest.exists())
        return True
    try:
        plan.record("statusline.py", _place(src, dest, mode))
    except OSError as e:
        plan.failed.append("statusline.py")
        log(f"⚠️  statusline.py の配置に失敗しました: {e}", quiet)
        return False
    if mode == "copy" and platform.system() != "Windows":
        dest.chmod(0o755)
    return True


def _is_ours(dest: Path, prev_mode: str, roots) -> bool:
    """撤去してよいか＝本スクリプトが置いたものが手つかずで残っているか。

    判定は**記録した配布元パスとの照合**で行う。「パスに claude-toolkit を含むか」
    のような文字列ヒューリスティクスは、配布元のディレクトリ名が違うだけで黙って
    外れ、撤去し損ねる（撤去漏れはまさに直したい失敗様式なので使わない）。
    """
    if dest.is_symlink():
        target = Path(os.readlink(dest))
        return any(target == r or r in target.parents for r in roots)
    # copy 配置なら実体しか残らないので、記録があること自体を根拠にする。
    # symlink 配置だったのに実体になっている＝ユーザーが差し替えたので触らない。
    return prev_mode == "copy"


def remove_orphans(plan: Plan, prev: dict, root: Path, dry: bool, quiet: bool):
    """前回置いたが今回は置かなかったものを撤去する。

    これが無いと、上流で廃止した Skill・規約が端末に残り続け、消したはずのものが
    読まれる。旧4実装はいずれも「置き直す」だけで撤去を持っていなかった。
    """
    prev_mode = prev.get("mode", "symlink")
    roots = {root}
    if prev.get("root"):
        roots.add(Path(prev["root"]))

    def drop(rel):
        dest = CLAUDE / rel
        if not (dest.exists() or dest.is_symlink()):
            return
        if not _is_ours(dest, prev_mode, roots):
            log(f"ℹ️  {rel} は別の実体に差し替えられているため残します", quiet)
            return
        plan.removed.append(rel)
        if not dry:
            try:
                _clear(dest)
            except OSError as e:
                plan.failed.append(rel)
                log(f"⚠️  {rel} の撤去に失敗しました: {e}", quiet)

    for rel in sorted(set(prev.get("placed", [])) - set(plan.placed)):
        drop(rel)

    # マニフェストが無い時代（旧インストーラ）からの移行では上の突き合わせが効かない。
    # skills だけは名前で走査できるので、配布元を指す迷子を落とす。
    dest_dir = CLAUDE / "skills"
    if not dest_dir.is_dir():
        return
    current = {p.split("/", 1)[1] for p in plan.placed if p.startswith("skills/")}
    for entry in sorted(dest_dir.iterdir()):
        rel = f"skills/{entry.name}"
        if entry.name in current or rel in plan.removed or not entry.is_symlink():
            continue
        # 旧実装が別の配布元（vendor 等）から張った残骸も拾う
        target = Path(os.readlink(entry))
        if not any(r in target.parents or r == target for r in roots) and "claude-toolkit" not in str(target):
            continue
        plan.removed.append(rel)
        if not dry:
            entry.unlink()


# --- settings.json --------------------------------------------------------

def _windows_short_path(path: str) -> str:
    """空白を含まない 8.3 短縮パスへ変換する。取れなければ元のパスを返す。

    ボリュームによっては 8.3 名の生成が無効化されており、その場合は長いパスが
    そのまま返る。呼び出し側で空白が残っていないか確認すること。
    """
    try:
        import ctypes
        from ctypes import wintypes

        fn = ctypes.windll.kernel32.GetShortPathNameW
        fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        fn.restype = wintypes.DWORD
        need = fn(path, None, 0)
        if not need:
            return path
        buf = ctypes.create_unicode_buffer(need)
        if not fn(path, buf, need):
            return path
        return buf.value or path
    except (ImportError, AttributeError, OSError):
        return path


def _windows_statusline_exe() -> str:
    return _windows_short_path(sys.executable).replace("\\", "/")


def _statusline_command() -> str:
    if platform.system() == "Windows":
        # POSIX の存在ガードが書けないため、絶対パスで直接指す。
        #
        # Claude Code は Windows の statusLine を Git Bash（あれば）か、無ければ
        # PowerShell（-NoProfile -NonInteractive -Command）で実行する。cmd は経由
        # しない（2026-08-01、CLI 2.1.87 の実装で確認）。この2つはクォートの要求が
        # 正反対で、両方で動く形は「実行ファイルは裸・引数はクォート」しかない。
        #   bash       : バックスラッシュはエスケープとして食われて消え
        #                command not found になる（311C4W991 で実機確認）ため / に
        #                統一する。空白入りパスにはクォートが要る。
        #   PowerShell : `"...exe" "..."` は式として解釈され Unexpected token で
        #                落ちる。実行ファイルをクォートするなら `&` の前置が要るが、
        #                それは bash 側で構文エラーになる。
        # そこで実行ファイルは 8.3 短縮パスにして空白そのものを消し、裸で置く。
        # 引数側のクォートはどちらのシェルでも素通しなので、こちらは残す。
        py = _windows_statusline_exe()
        script = str(CLAUDE / "statusline.py").replace("\\", "/")
        return f'{py} "{script}"'
    # statusline.py が無い／リンクが切れていてもエラーにしない。
    return "[ -f ~/.claude/statusline.py ] && python3 ~/.claude/statusline.py || true"


def ensure_statusline_setting(dry: bool, quiet: bool):
    """statusLine を追記する。未設定なら追記、本スクリプトが書いた値なら更新。

    無条件の上書きはしない。別の仕組みで配線している環境の設定を壊し、各自が手で
    変えた値も消える（本人環境で2度発生した）。一方で「既にあれば一切触らない」だけ
    だと、旧実装が書いた古い形（3種類に分裂していた）が永久に直らないため、
    **statusline.py を指している値に限って**最新の形へ寄せる。
    """
    path = CLAUDE / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        settings = {}

    want = _statusline_command()
    if platform.system() == "Windows" and " " in _windows_statusline_exe():
        log(
            "⚠️  Python の実行ファイルパスに空白があり、8.3 短縮名も取得できませんでした。"
            "statusLine が表示されない場合は空白を含まない場所へ Python を入れ直してください",
            quiet,
        )
    cur = settings.get("statusLine")
    if isinstance(cur, dict):
        cur_cmd = str(cur.get("command", ""))
        if cur_cmd == want:
            log("ℹ️  settings.json の statusLine は最新です", quiet)
            return
        if "statusline.py" not in cur_cmd:
            log("ℹ️  settings.json の statusLine は別の仕組みのため変更しません", quiet)
            return
        action = "更新"
    else:
        action = "追記"

    if dry:
        log(f"（dry-run）settings.json の statusLine を{action}します", quiet)
        return
    settings["statusLine"] = {"type": "command", "command": want}
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"✅ settings.json の statusLine を{action}しました", quiet)


def ensure_claude_md(inst, dry: bool):
    """instructions/ を読ませる @行を ~/.claude/CLAUDE.md に用意する。

    無ければ作る。あれば書き換えず、足りない行だけを表示する。既存の CLAUDE.md は
    各自が育てているファイルで、行の順序や前後の文脈に意味があるため自動で触らない。

    従来は初回のみ「次を足してください」と案内していた。しかし CLAUDE.md が無くても
    配線の記録さえあれば2回目以降は何も出ないため、**一度案内を見送った端末では
    instructions/ が永久に読まれないまま**になっていた（2026-08-15 に確認）。
    案内を毎回出すのではなく、実際に足りないときだけ出す形にする。
    """
    path = CLAUDE / "CLAUDE.md"
    want = [f"@instructions/{md.name}" for md in inst]

    if not path.exists():
        if dry:
            print()
            print(f"（dry-run）{path} を作成し、@行を {len(want)} 件書きます")
            return
        path.write_text("\n".join(want) + "\n", encoding="utf-8")
        print()
        print(f"✅ ~/.claude/CLAUDE.md を作成しました（@行 {len(want)} 件）")
        return

    body = path.read_text(encoding="utf-8", errors="replace")
    missing = [line for line in want if line not in body]
    if not missing:
        return
    print()
    print("instructions/ は置くだけでは読まれません。~/.claude/CLAUDE.md に次を足してください:")
    for line in missing:
        print(f"  {line}")


# --- エントリポイント -----------------------------------------------------

def main() -> int:
    default_mode = "copy" if platform.system() == "Windows" else "symlink"
    p = argparse.ArgumentParser(description="claude-toolkit を ~/.claude/ へ配置する")
    p.add_argument("--root", default=str(Path(__file__).resolve().parent))
    p.add_argument("--mode", choices=("symlink", "copy"), default=default_mode)
    p.add_argument("--label", default="claude-toolkit/install.py")
    p.add_argument("--no-settings", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args()

    root = Path(a.root).resolve()
    if not (root / "skills").is_dir():
        print(f"エラー: claude-toolkit の中身が見つかりません: {root}")
        return 1

    CLAUDE.mkdir(parents=True, exist_ok=True)
    if not check_owner(root, a.label, a.force, a.quiet):
        return 2

    prev = read_marker()
    plan = Plan()
    place_skills(root, a.mode, plan, a.dry_run, a.quiet)
    place_md_dirs(root, a.mode, plan, a.dry_run, a.quiet)
    has_sl = place_statusline(root, a.mode, plan, a.dry_run, a.quiet)
    remove_orphans(plan, prev, root, a.dry_run, a.quiet)
    if not a.no_settings and has_sl:
        ensure_statusline_setting(a.dry_run, a.quiet)

    if not a.dry_run:
        write_marker(root, a.label, a.mode, plan.placed)

    if a.quiet:
        return 1 if plan.failed else 0

    verb = "symlink" if a.mode == "symlink" else "コピー"
    head = "（dry-run）" if a.dry_run else ""
    first = not prev
    print(f"{head}claude-toolkit を配置しました（{verb}・配布元 {root}）")
    print(f"  ~/.claude/skills/        {sum(1 for r in plan.placed if r.startswith('skills/'))} 件")
    print(f"  ~/.claude/conventions/   {sum(1 for r in plan.placed if r.startswith('conventions/'))} 件（作業種別ごとの規約）")
    print(f"  ~/.claude/               {sum(1 for r in plan.placed if '/' not in r and r != 'statusline.py')} 件（参照文書 SESSION-END.md 等）")
    print(f"  ~/.claude/instructions/  {sum(1 for r in plan.placed if r.startswith('instructions/'))} 件（グローバル指示の断片）")
    if has_sl:
        print("  ~/.claude/statusline.py")

    if not first:
        print()
        print(f"前回からの差分: 追加 {len(plan.added)} / 撤去 {len(plan.removed)}")
        for rel in plan.added:
            print(f"  + {rel}")
        for rel in plan.removed:
            print(f"  - {rel}（上流で廃止）")
        if not plan.added and not plan.removed:
            print("  （構成の変化なし。symlink 配置の中身は git pull で既に最新）"
                  if a.mode == "symlink" else "  （構成の変化なし。中身は再コピー済み）")

    inst = sorted((root / "instructions").glob("*.md")) if (root / "instructions").is_dir() else []
    inst = [m for m in inst if m.name != "README.md"]
    if inst:
        ensure_claude_md(inst, a.dry_run)

    if plan.failed:
        print()
        print(f"⚠️  {len(plan.failed)} 件が失敗しました: {', '.join(plan.failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
