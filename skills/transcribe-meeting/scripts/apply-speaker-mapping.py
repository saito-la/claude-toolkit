#!/usr/bin/env python3
"""トランスクリプトの話者ラベル（話者A/B…）を実名＋確度記号へ一括置換する。

自動文字起こしの話者ラベルはチャンク（## Part N）ごとに割り当てが入れ替わるため、
Part 単位で対応表を与える。置換後に Part ヘッダーと区切り線を除去して1本のトランス
クリプトに整える。

使い方:
  apply-speaker-mapping.py <transcript.txt> --mapping mapping.json
  apply-speaker-mapping.py <transcript.txt> --mapping mapping.json --dry-run
  apply-speaker-mapping.py <transcript.txt> --mapping mapping.json --keep-parts

mapping.json の形式（キーは Part 番号の文字列）:
  {
    "1": {"話者A": "古賀部長〔◎〕", "話者B": "小西〔○〕"},
    "2": {"話者A": "小西〔○〕",     "話者B": "金子〔△・AMED担当と推定〕"}
  }

Part 分割が無い（未分離＝全編が話者A）トランスクリプトには、キー "0" の対応表を
全体に適用する:
  {"0": {"話者A": "古賀部長〔◎〕"}}
"""

import argparse, json, re, shutil, sys
from pathlib import Path

PART_RE = re.compile(r'(?=## Part \d+)')
PART_NUM_RE = re.compile(r'## Part (\d+)')


def apply_mapping(content, mapping, keep_parts=False):
    """Part ごとに話者ラベルを置換し、置換件数を返す。"""
    replaced = 0
    global_map = mapping.get('0', {})
    sections = PART_RE.split(content)
    out = []
    for sec in sections:
        m = PART_NUM_RE.match(sec)
        part_map = dict(global_map)
        if m:
            part_map.update(mapping.get(m.group(1), {}))
        for speaker, name in part_map.items():
            # 「話者A:」の形だけを置換する（本文中の言及は触らない）
            needle = f'{speaker}:'
            replaced += sec.count(needle)
            sec = sec.replace(needle, f'{name}:')
        out.append(sec)
    content = ''.join(out)
    if not keep_parts:
        content = re.sub(r'\n*---\n*', '\n', content)
        content = re.sub(r'\n*## Part \d+[^\n]*\n*', '\n', content)
    return content.strip() + '\n', replaced


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('transcript', type=Path)
    ap.add_argument('--mapping', type=Path, required=True, help='Part 番号→話者ラベル対応表のJSON')
    ap.add_argument('--dry-run', action='store_true', help='書き換えずに結果を標準出力へ')
    ap.add_argument('--keep-parts', action='store_true', help='## Part ヘッダーと区切り線を残す')
    ap.add_argument('--no-backup', action='store_true', help='.bak を作らない')
    args = ap.parse_args()

    if not args.transcript.is_file():
        sys.exit(f'トランスクリプトが見つかりません: {args.transcript}')
    if not args.mapping.is_file():
        sys.exit(f'対応表が見つかりません: {args.mapping}')

    try:
        mapping = json.loads(args.mapping.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        sys.exit(f'対応表のJSONが不正です: {e}')
    if not isinstance(mapping, dict) or not all(isinstance(v, dict) for v in mapping.values()):
        sys.exit('対応表は {"Part番号": {"話者A": "実名〔確度〕"}} の形にしてください')

    content = args.transcript.read_text(encoding='utf-8')
    result, replaced = apply_mapping(content, mapping, keep_parts=args.keep_parts)

    if args.dry_run:
        sys.stdout.write(result)
        print(f'\n--- dry-run: {replaced} 箇所を置換予定 ---', file=sys.stderr)
        return

    if replaced == 0:
        print('警告: 置換が0件でした。対応表の話者ラベルがトランスクリプトの表記と一致しているか確認してください。', file=sys.stderr)
    if not args.no_backup:
        shutil.copy2(args.transcript, args.transcript.with_suffix(args.transcript.suffix + '.bak'))
    args.transcript.write_text(result, encoding='utf-8')
    print(f'{args.transcript}: {replaced} 箇所を置換しました')


if __name__ == '__main__':
    main()
