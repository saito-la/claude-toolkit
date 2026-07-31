#!/usr/bin/env python3
"""rtk の設定を Claude Code から安全に取り除く。

既定は検査のみで何も書き換えない。実際に削除するときだけ --apply を付ける。
書き換える前に必ずバックアップを作る。

  python3 remove-rtk.py            # 何が残っているかを表示するだけ
  python3 remove-rtk.py --apply    # バックアップを作って削除を実行

対象は ~/.claude/ 配下の settings.json・settings.local.json・CLAUDE.md・RTK.md。
rtk 本体のアンインストールは行わない（README の手順を参照）。
"""

import argparse
import datetime
import json
import shutil
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"


def stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def backup(path: Path) -> Path:
    dest = path.with_name(f"{path.name}.bak-{stamp()}")
    shutil.copy2(path, dest)
    return dest


def mentions_rtk(value) -> bool:
    """入れ子の dict/list を辿って rtk への言及を探す。"""
    if isinstance(value, str):
        return "rtk" in value.lower()
    if isinstance(value, dict):
        return any(mentions_rtk(v) for v in value.values())
    if isinstance(value, list):
        return any(mentions_rtk(v) for v in value)
    return False


def clean_settings(path: Path, findings: list) -> dict | None:
    """settings.json から rtk 由来の設定を除いた dict を返す。変更が無ければ None。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        findings.append(f"[!] {path} は JSON として読めないため手を触れない: {exc}")
        return None

    changed = False

    # 1. hooks の各イベントから rtk を呼ぶエントリを取り除く。
    #    rtk init -g --auto-patch は PreToolUse に追記するが、重複登録されている
    #    ことがあるため「rtk に言及する全エントリ」を対象にする。
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event in list(hooks.keys()):
            groups = hooks.get(event)
            if not isinstance(groups, list):
                continue
            kept_groups = []
            for group in groups:
                if not isinstance(group, dict):
                    kept_groups.append(group)
                    continue
                inner = group.get("hooks")
                if isinstance(inner, list):
                    kept_inner = [h for h in inner if not mentions_rtk(h)]
                    removed = len(inner) - len(kept_inner)
                    if removed:
                        changed = True
                        findings.append(
                            f"  hooks.{event}: rtk を呼ぶフック {removed} 件を削除"
                        )
                    if not kept_inner:
                        # そのマッチャに残る処理が無くなったらグループごと落とす
                        continue
                    group = {**group, "hooks": kept_inner}
                elif mentions_rtk(group):
                    changed = True
                    findings.append(f"  hooks.{event}: rtk のエントリを削除")
                    continue
                kept_groups.append(group)
            if kept_groups:
                hooks[event] = kept_groups
            else:
                del hooks[event]
                changed = True
                findings.append(f"  hooks.{event}: 中身が空になったのでキーごと削除")
        if not hooks:
            del data["hooks"]
            changed = True

    # 2. permissions.allow から rtk 用の許可を取り除く。
    perms = data.get("permissions")
    if isinstance(perms, dict):
        for key in ("allow", "deny", "ask"):
            entries = perms.get(key)
            if not isinstance(entries, list):
                continue
            kept = [e for e in entries if not (isinstance(e, str) and "rtk" in e.lower())]
            if len(kept) != len(entries):
                changed = True
                findings.append(
                    f"  permissions.{key}: rtk 用の許可 {len(entries) - len(kept)} 件を削除"
                )
                perms[key] = kept

    return data if changed else None


def clean_claude_md(path: Path, findings: list) -> str | None:
    """CLAUDE.md から @RTK.md のインポート行を取り除く。変更が無ければ None。"""
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [l for l in lines if l.strip() != "@RTK.md"]
    if len(kept) == len(lines):
        return None
    findings.append(f"  {path.name}: @RTK.md のインポート行 {len(lines) - len(kept)} 件を削除")
    return "".join(kept)


def main() -> int:
    ap = argparse.ArgumentParser(description="rtk の設定を Claude Code から取り除く")
    ap.add_argument("--apply", action="store_true", help="実際に書き換える（既定は検査のみ）")
    args = ap.parse_args()

    if not CLAUDE_DIR.is_dir():
        print(f"{CLAUDE_DIR} が無い。Claude Code の設定ディレクトリを確認すること。")
        return 1

    findings: list[str] = []
    writes: list[tuple[Path, str]] = []
    deletes: list[Path] = []

    for name in ("settings.json", "settings.local.json"):
        path = CLAUDE_DIR / name
        cleaned = clean_settings(path, findings)
        if cleaned is not None:
            writes.append((path, json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n"))

    md = CLAUDE_DIR / "CLAUDE.md"
    cleaned_md = clean_claude_md(md, findings)
    if cleaned_md is not None:
        writes.append((md, cleaned_md))

    rtk_md = CLAUDE_DIR / "RTK.md"
    if rtk_md.exists() or rtk_md.is_symlink():
        findings.append(f"  {rtk_md.name}: 削除対象（rtk init が生成したもの）")
        deletes.append(rtk_md)

    if not findings:
        print("rtk 由来の設定は見つからなかった。対応は不要。")
        return 0

    print("rtk 由来の設定が見つかった:")
    for f in findings:
        print(f)

    if not args.apply:
        print("\n検査のみ。実際に削除するには --apply を付けて再実行する。")
        return 0

    print("")
    for path, content in writes:
        b = backup(path)
        path.write_text(content, encoding="utf-8")
        print(f"更新: {path}（バックアップ: {b.name}）")
    for path in deletes:
        if path.is_symlink():
            path.unlink()
            print(f"削除: {path}（symlink）")
        else:
            b = backup(path)
            path.unlink()
            print(f"削除: {path}（バックアップ: {b.name}）")

    print("\n残りの手順:")
    print("  1. rtk 本体を消す: brew uninstall rtk")
    print("     （curl でインストールした場合は ~/.local/bin/rtk を削除）")
    print("  2. Claude Code を再起動する")
    print("  3. python3 remove-rtk.py を再実行し「対応は不要」と出ることを確認する")
    return 0


if __name__ == "__main__":
    sys.exit(main())
