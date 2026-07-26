#!/usr/bin/env python3
"""Pattern 3: Ring meter - pie-like circle segments"""
import json, sys, os, subprocess, time
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

data = json.load(sys.stdin)

R = '\033[0m'
DIM = '\033[2m'
BOLD = '\033[1m'

RINGS = ['○', '◔', '◑', '◕', '●']

def gradient(pct):
    if pct < 50:
        r = int(pct * 5.1)
        return f'\033[38;2;{r};200;80m'
    else:
        g = int(200 - (pct - 50) * 4)
        return f'\033[38;2;255;{max(g, 0)};60m'

def ring(pct):
    idx = min(int(pct / 25), 4)
    return RINGS[idx]

def fmt_ctx(pct):
    p = round(pct)
    return f'{DIM}ctx{R} {gradient(pct)}{ring(pct)} {p}%{R}'

def fmt_rate(label, pct, resets_at, time_fmt):
    p = round(pct)
    if resets_at:
        reset_str = datetime.fromtimestamp(resets_at).strftime(time_fmt)
        label_str = f'{DIM}{label}({reset_str}){R}'
    else:
        label_str = f'{DIM}{label}{R}'
    return f'{label_str} {gradient(pct)}{ring(pct)} {p}%{R}'

model = data.get('model', {}).get('display_name', 'Claude')
# Remove "Claude " prefix if present for compactness
model = model.removeprefix('Claude ')
parts = [f'{BOLD}{model}{R}']

ctx = data.get('context_window', {}).get('used_percentage')
if ctx is not None:
    parts.append(fmt_ctx(ctx))

five_data = data.get('rate_limits', {}).get('five_hour', {})
five = five_data.get('used_percentage')
if five is not None:
    parts.append(fmt_rate('5h', five, five_data.get('resets_at'), '%H:%M'))

week_data = data.get('rate_limits', {}).get('seven_day', {})
week = week_data.get('used_percentage')
if week is not None:
    # ゼロ埋めなしの月/日指定子はOS依存（Unix系: %-m/%-d、Windows: %#m/%#d）
    date_fmt = '%#m/%#d' if sys.platform == 'win32' else '%-m/%-d'
    parts.append(fmt_rate('7d', week, week_data.get('resets_at'), date_fmt))

cwd = data.get('cwd') or data.get('workspace', {}).get('current_dir', '')
folder = os.path.basename(cwd) if cwd else '-'
parts.append(f'{BOLD}{folder}{R}')

CACHE_FILE = os.path.expanduser('~/.claude/.auth-cache')
LABELS_FILE = os.path.expanduser('~/.claude/.account-labels.json')
CACHE_TTL = 600  # 10分

def get_account_label():
    try:
        labels = json.load(open(LABELS_FILE, encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        labels = {}

    # キャッシュが有効なら即返す
    try:
        if time.time() - os.path.getmtime(CACHE_FILE) < CACHE_TTL:
            email = open(CACHE_FILE, encoding='utf-8').read().strip()
            return labels.get(email, email.split('@')[0] if email else '-')
    except FileNotFoundError:
        pass

    # キャッシュ期限切れ/なし → バックグラウンドで更新（sh依存を避け、Pure Pythonで両OS対応）
    cache_script = (
        'import subprocess,json,pathlib,sys;'
        'r=subprocess.run(["claude","auth","status"],capture_output=True,text=True,encoding="utf-8");'
        'd=json.loads(r.stdout) if r.returncode==0 else {};'
        f'pathlib.Path(r"{CACHE_FILE}").write_text(d.get("email",""),encoding="utf-8")'
    )
    popen_kwargs = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    if sys.platform == 'win32':
        popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen([sys.executable, '-c', cache_script], **popen_kwargs)

    # 古いキャッシュがあればそれを返す、なければ '-'
    try:
        email = open(CACHE_FILE, encoding='utf-8').read().strip()
        return labels.get(email, email.split('@')[0] if email else '-')
    except FileNotFoundError:
        return '-'

parts.append(f'{DIM}{get_account_label()}{R}')

print('  '.join(parts), end='')
