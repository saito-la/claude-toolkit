#!/usr/bin/env python3
"""box-api — Box API を、アカウントごとのトークンで直接呼ぶ小さな呼び出し口。

Box CLI の代わりに使う。Box CLI は全アカウントの設定を Keychain の1項目にまとめ、
「既定のアカウント」を切り替えて使う作りで、並行に動くと全アカウントの設定を失う
（2026-11-24 に実際に起きた）。box-api は共有の「既定」を持たず、アカウントごとに
別のファイルへトークンを置くので、同時に動いても互いに壊さず、ssh からも動く。

使い方:
  box-api accounts                                  設定済みのアカウントを一覧する
  box-api login <account> [--client-id ID] [--port N]
                                                    ブラウザでログインしてトークンを得る
  box-api <account> whoami                          ログイン中のユーザーを表示する
  box-api <account> get|post|put|delete <path> [--query k=v ...] [--data JSON|@file]
                                                    API を1回呼び、応答の JSON を出す
            [--all]                                 一覧の API を最後のページまで読んで entries をまとめる
  box-api <account> download <file_id> <保存先>     ファイルを保存する（保存先がディレクトリなら元の名前で）
  box-api <account> upload <folder_id> <ファイル> [--name 名前]
                                                    フォルダへ新しいファイルとして上げる
  box-api <account> upload-version <file_id> <ファイル>
                                                    既存のファイルに新しい版として上げる

設定とトークン: ~/.config/box-api/<account>.json（権限 600）。client_id・client_secret・
refresh_token・access_token を持つ。秘匿値なので画面に出さない・リポジトリに置かない。
Box の refresh_token は使うたびに新しくなり古い方は無効になるので、同じアカウントを
複数の Mac で使うときは、Mac ごとに login する（ファイルを写して共用しない）。

アプリの準備: 各テナントの開発者コンソールで Platform App（User Authentication / OAuth 2.0）を
作り、Redirect URI に http://localhost:<port>/callback（既定 3000）を登録する。
"""
import argparse
import fcntl
import getpass
import http.server
import json
import mimetypes
import os
import secrets
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser

CONF_DIR = os.path.expanduser(os.environ.get("BOX_API_CONFIG_DIR", "~/.config/box-api"))
API = os.environ.get("BOX_API_BASE", "https://api.box.com/2.0")
UPLOAD = os.environ.get("BOX_UPLOAD_BASE", "https://upload.box.com/api/2.0")
OAUTH = os.environ.get("BOX_OAUTH_BASE", "https://api.box.com/oauth2")
AUTHORIZE = os.environ.get("BOX_AUTHORIZE_URL", "https://account.box.com/api/oauth2/authorize")
DEFAULT_PORT = 3000  # Box CLI の既定と同じ。既存の OAuth アプリに登録済みのことが多い
REFRESH_MARGIN = 300  # 失効の5分前に更新する


def die(msg, code=1):
    print("box-api: " + msg, file=sys.stderr)
    sys.exit(code)


# ---- 設定ファイル（アカウントごと） --------------------------------------------

def conf_path(account):
    if not account or "/" in account or account.startswith("."):
        die("アカウント名が不正: %r" % account, 2)
    return os.path.join(CONF_DIR, account + ".json")


def load(account):
    p = conf_path(account)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        die("アカウント %s の設定が無い（box-api login %s）" % (account, account))


def save(account, data):
    """一時ファイルに書いてから rename する。途中で落ちても元のファイルは壊れない。"""
    os.makedirs(CONF_DIR, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CONF_DIR, prefix="." + account + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, conf_path(account))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class AccountLock:
    """アカウントごとの排他（flock）。トークンの更新を1プロセスずつにする。"""

    def __init__(self, account):
        os.makedirs(CONF_DIR, mode=0o700, exist_ok=True)
        self.path = os.path.join(CONF_DIR, "." + account + ".lock")

    def __enter__(self):
        self.f = open(self.path, "a")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


# ---- HTTP ---------------------------------------------------------------------

def http_request(method, url, headers=None, body=None, allow_redirect=True):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    opener = urllib.request.build_opener() if allow_redirect else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=300) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def token_request(fields):
    body = urllib.parse.urlencode(fields).encode()
    st, _, data = http_request("POST", OAUTH + "/token", {"Content-Type": "application/x-www-form-urlencoded"}, body)
    if st != 200:
        raise RuntimeError("トークンの取得に失敗（HTTP %s）: %s" % (st, data.decode("utf-8", "replace")[:300]))
    return json.loads(data)


def store_tokens(conf, tok):
    conf["access_token"] = tok["access_token"]
    conf["refresh_token"] = tok["refresh_token"]
    conf["expires_at"] = int(time.time()) + int(tok.get("expires_in", 3600))


def access_token(account, force=False):
    conf = load(account)
    if not force and conf.get("access_token") and conf.get("expires_at", 0) - REFRESH_MARGIN > time.time():
        return conf["access_token"]
    with AccountLock(account):
        conf = load(account)  # 待っている間に別のプロセスが更新していれば、それを使う
        if not force and conf.get("access_token") and conf.get("expires_at", 0) - REFRESH_MARGIN > time.time():
            return conf["access_token"]
        if force and conf.get("access_token") and conf.get("refreshed_at", 0) > time.time() - 30:
            return conf["access_token"]  # 直前に別のプロセスが更新した
        if not conf.get("refresh_token"):
            die("アカウント %s はログインしていない（box-api login %s）" % (account, account))
        try:
            tok = token_request({
                "grant_type": "refresh_token",
                "refresh_token": conf["refresh_token"],
                "client_id": conf["client_id"],
                "client_secret": conf["client_secret"],
            })
        except RuntimeError as e:
            die("%s。60日使わないと失効する。box-api login %s で入り直す" % (e, account))
        store_tokens(conf, tok)
        conf["refreshed_at"] = int(time.time())
        save(account, conf)
        return conf["access_token"]


def call(account, method, url, headers=None, body=None, allow_redirect=True):
    """401 なら1回だけ更新してやり直す。429・5xx は待ってやり直す。"""
    headers = dict(headers or {})
    force = False
    for attempt in range(5):
        headers["Authorization"] = "Bearer " + access_token(account, force=force)
        st, h, data = http_request(method, url, headers, body, allow_redirect)
        if st == 401 and not force:
            force = True
            continue
        if st == 429 or st >= 500:
            wait = float(h.get("Retry-After") or h.get("retry-after") or 2 ** attempt)
            time.sleep(min(wait, 60))
            continue
        return st, h, data
    return st, h, data


def api_url(path, query):
    if path.startswith("http"):
        url = path
    else:
        url = API + "/" + path.lstrip("/")
    if query:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    return url


def check(st, data):
    if st >= 400:
        sys.stderr.write(data.decode("utf-8", "replace") + "\n")
        die("HTTP %s" % st, 1)


# ---- コマンド -----------------------------------------------------------------

def cmd_accounts(_):
    if not os.path.isdir(CONF_DIR):
        return
    for name in sorted(os.listdir(CONF_DIR)):
        if name.endswith(".json") and not name.startswith("."):
            c = load(name[:-5])
            state = "ログイン済み" if c.get("refresh_token") else "未ログイン"
            print("%s\t%s\t%s" % (name[:-5], c.get("login", "?"), state))


def cmd_login(a):
    p = conf_path(a.account)
    conf = {}
    if os.path.exists(p):
        conf = load(a.account)
    if a.client_id:
        conf["client_id"] = a.client_id
    if not conf.get("client_id"):
        conf["client_id"] = input("Client ID: ").strip()
    if a.client_id or not conf.get("client_secret"):
        if sys.stdin.isatty():
            conf["client_secret"] = getpass.getpass("Client Secret（入力しても表示されない）: ").strip()
        else:
            conf["client_secret"] = sys.stdin.readline().strip()  # パイプで渡されたとき
    port = a.port or conf.get("port") or DEFAULT_PORT
    conf["port"] = port
    redirect = "http://localhost:%d/callback" % port
    state = secrets.token_urlsafe(16)
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if urllib.parse.urlparse(self.path).path != "/callback":
                self.send_response(404); self.end_headers(); return
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("box-api: 受け取りました。このタブは閉じてよい。".encode())

        def log_message(self, *x):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    th = threading.Thread(target=srv.handle_request, daemon=True)
    th.start()
    url = AUTHORIZE + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": conf["client_id"], "redirect_uri": redirect, "state": state})
    print("ブラウザでログインする（開かなければこの URL を開く）:\n" + url, file=sys.stderr)
    if not a.no_browser:
        webbrowser.open(url)
    th.join(timeout=600)
    srv.server_close()
    if not got:
        die("10分待っても戻ってこなかった")
    if got.get("state") != state:
        die("state が一致しない（別の要求の応答）。やり直す")
    if "code" not in got:
        die("認可されなかった: %s" % (got.get("error_description") or got.get("error")))
    with AccountLock(a.account):
        tok = token_request({"grant_type": "authorization_code", "code": got["code"],
                             "client_id": conf["client_id"], "client_secret": conf["client_secret"],
                             "redirect_uri": redirect})
        store_tokens(conf, tok)
        conf["refreshed_at"] = int(time.time())
        save(a.account, conf)
    st, _, data = call(a.account, "GET", api_url("users/me", {"fields": "login,name,role,enterprise"}))
    check(st, data)
    me = json.loads(data)
    with AccountLock(a.account):
        conf = load(a.account)
        conf["login"] = me.get("login")
        save(a.account, conf)
    print("%s: %s（%s, role=%s）でログインした" % (a.account, me.get("login"), me.get("name"), me.get("role")))


def cmd_whoami(a):
    st, _, data = call(a.account, "GET", api_url("users/me", {"fields": "login,name,role,enterprise"}))
    check(st, data)
    print(data.decode())


def parse_query(items):
    q = []
    for it in items or []:
        if "=" not in it:
            die("--query は k=v の形: %r" % it, 2)
        q.append(tuple(it.split("=", 1)))
    return q


def read_data(d):
    if d is None:
        return None
    if d.startswith("@"):
        with open(d[1:], "rb") as f:
            return f.read()
    return d.encode()


def cmd_request(a):
    method = a.cmd.upper()
    query = parse_query(a.query)
    body = read_data(a.data)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if not a.all:
        st, _, data = call(a.account, method, api_url(a.path, query), headers, body)
        check(st, data)
        sys.stdout.write(data.decode("utf-8", "replace"))
        if data and not data.endswith(b"\n"):
            sys.stdout.write("\n")
        return
    # 一覧の API を最後まで読む（marker 方式と offset 方式の両方）
    entries, base = [], [kv for kv in query if kv[0] not in ("offset", "marker")]
    marker, offset = None, 0
    use_marker = any(k == "usemarker" for k, _ in query)
    while True:
        q = list(base)
        if marker:
            q.append(("marker", marker))
        elif not use_marker and offset:
            q.append(("offset", str(offset)))
        st, _, data = call(a.account, method, api_url(a.path, q), headers, body)
        check(st, data)
        page = json.loads(data)
        got = page.get("entries", [])
        entries.extend(got)
        marker = page.get("next_marker")
        if marker:
            continue
        total = page.get("total_count")
        offset += len(got)
        if use_marker or not got or total is None or offset >= total:
            break
    json.dump({"total_count": len(entries), "entries": entries}, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


def cmd_download(a):
    dest = a.dest
    if os.path.isdir(dest):
        st, _, data = call(a.account, "GET", api_url("files/" + a.file_id, {"fields": "name"}))
        check(st, data)
        dest = os.path.join(dest, json.loads(data)["name"])
    st, h, data = call(a.account, "GET", api_url("files/%s/content" % a.file_id, None))
    check(st, data)
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)
    print(dest)


def multipart(attributes, path):
    boundary = uuid.uuid4().hex
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        content = f.read()
    parts = [
        ("--%s\r\nContent-Disposition: form-data; name=\"attributes\"\r\n\r\n" % boundary).encode()
        + json.dumps(attributes, ensure_ascii=False).encode() + b"\r\n",
        ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
         % (boundary, urllib.parse.quote(name), ctype)).encode() + content + b"\r\n",
        ("--%s--\r\n" % boundary).encode(),
    ]
    return {"Content-Type": "multipart/form-data; boundary=" + boundary}, b"".join(parts)


def cmd_upload(a):
    if os.path.getsize(a.src) > 50 * 1024 * 1024:
        die("50MB を超えるファイルは分割アップロードが要る（未対応）")
    attrs = {"name": a.name or os.path.basename(a.src), "parent": {"id": a.folder_id}}
    h, body = multipart(attrs, a.src)
    st, _, data = call(a.account, "POST", UPLOAD + "/files/content", h, body)
    check(st, data)
    print(data.decode())


def cmd_upload_version(a):
    if os.path.getsize(a.src) > 50 * 1024 * 1024:
        die("50MB を超えるファイルは分割アップロードが要る（未対応）")
    h, body = multipart({"name": os.path.basename(a.src)}, a.src)
    st, _, data = call(a.account, "POST", UPLOAD + "/files/%s/content" % a.file_id, h, body)
    check(st, data)
    print(data.decode())


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    if argv[0] == "accounts":
        return cmd_accounts(None)
    if argv[0] == "login":
        p = argparse.ArgumentParser(prog="box-api login")
        p.add_argument("account")
        p.add_argument("--client-id")
        p.add_argument("--port", type=int)
        p.add_argument("--no-browser", action="store_true")
        return cmd_login(p.parse_args(argv[1:]))
    account, rest = argv[0], argv[1:]
    if not rest:
        die("コマンドが無い（box-api --help）", 2)
    cmd = rest[0]
    p = argparse.ArgumentParser(prog="box-api %s %s" % (account, cmd))
    if cmd == "whoami":
        a = p.parse_args(rest[1:]); a.account = account
        return cmd_whoami(a)
    if cmd in ("get", "post", "put", "delete"):
        p.add_argument("path")
        p.add_argument("--query", action="append")
        p.add_argument("--data")
        p.add_argument("--all", action="store_true")
        a = p.parse_args(rest[1:]); a.account = account; a.cmd = cmd
        return cmd_request(a)
    if cmd == "download":
        p.add_argument("file_id"); p.add_argument("dest")
        a = p.parse_args(rest[1:]); a.account = account
        return cmd_download(a)
    if cmd == "upload":
        p.add_argument("folder_id"); p.add_argument("src"); p.add_argument("--name")
        a = p.parse_args(rest[1:]); a.account = account
        return cmd_upload(a)
    if cmd == "upload-version":
        p.add_argument("file_id"); p.add_argument("src")
        a = p.parse_args(rest[1:]); a.account = account
        return cmd_upload_version(a)
    die("知らないコマンド: %s（box-api --help）" % cmd, 2)


if __name__ == "__main__":
    main(sys.argv[1:])
