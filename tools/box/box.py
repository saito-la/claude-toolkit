#!/usr/bin/env python3
"""box — Box API を、アカウントごとのトークンで直接呼ぶ小さな呼び出し口。

Box CLI の代わりに使う。Box CLI は全アカウントの設定を Keychain の1項目にまとめ、
「既定のアカウント」を切り替えて使う作りで、並行に動くと全アカウントの設定を失う
（2026-11-24 に実際に起きた）。box は共有の「既定」を持たず、アカウントごとに
別のファイルへトークンを置くので、同時に動いても互いに壊さず、ssh からも動く。

使い方:
  box accounts                                      設定済みのアカウントを一覧する
  box login <account> [--client-id ID] [--port N]
                                                    ブラウザでログインしてトークンを得る
  box <account> whoami                              ログイン中のユーザーを表示する
  box <account> get|post|put|delete <path> [--query k=v ...] [--data JSON|@file]
                                                    API を1回呼び、応答の JSON を出す
            [--all]                                 一覧の API を最後のページまで読んで entries をまとめる
  box <account> download <file_id> <保存先>         ファイルを保存する（保存先がディレクトリなら元の名前で）
  box <account> items <folder_id> [--fields type,id,name]
                                                    フォルダの中身を全件、JSON の配列で出す
  box <account> mkdir <parent_id> <名前> [--exist-ok]
                                                    フォルダを作る（--exist-ok なら既存を返す）
  box <account> upload <folder_id> <ファイル> [--name 名前] [--overwrite]
                                                    フォルダへ上げる（--overwrite なら同名に新しい版として）
  box <account> upload-version <file_id> <ファイル>
                                                    既存のファイルに新しい版として上げる
  box <account> upload-dir <parent_id> <フォルダ> [--overwrite]
                                                    手元のフォルダを丸ごと上げる（既にあるファイルは飛ばす）
  上げる・作るコマンドは、できたファイル／フォルダの JSON（id・name など）を1つ出す。

設定とトークン: ~/.config/box/<account>.json（権限 600）。client_id・client_secret・
refresh_token・access_token を持つ。秘匿値なので画面に出さない・リポジトリに置かない。
Box の refresh_token は使うたびに新しくなり古い方は無効になるので、同じアカウントを
複数の Mac で使うときは、Mac ごとに login する（ファイルを写して共用しない）。

アプリの準備: 各テナントの開発者コンソールで Platform App（User Authentication / OAuth 2.0）を
作り、Redirect URI に http://localhost:<port>/callback（既定 3000）を登録する。
"""
import argparse
import errno
import getpass
import http.server
import json
import mimetypes
import os
import secrets
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser

if os.name == "nt":
    import msvcrt
else:
    import fcntl

CONF_DIR = os.path.expanduser(os.environ.get("BOX_CONFIG_DIR", "~/.config/box"))
API = os.environ.get("BOX_API_BASE", "https://api.box.com/2.0")
UPLOAD = os.environ.get("BOX_UPLOAD_BASE", "https://upload.box.com/api/2.0")
OAUTH = os.environ.get("BOX_OAUTH_BASE", "https://api.box.com/oauth2")
AUTHORIZE = os.environ.get("BOX_AUTHORIZE_URL", "https://account.box.com/api/oauth2/authorize")
DEFAULT_PORT = 3000  # Box CLI の既定と同じ。既存の OAuth アプリに登録済みのことが多い
REFRESH_MARGIN = 300  # 失効の5分前に更新する


def die(msg, code=1):
    print("box: " + msg, file=sys.stderr)
    sys.exit(code)


# ---- 設定ファイル（アカウントごと） --------------------------------------------

def conf_path(account):
    if not account or any(c in account for c in '/\\:') or account.startswith("."):
        die("アカウント名が不正: %r" % account, 2)
    return os.path.join(CONF_DIR, account + ".json")


def load(account):
    p = conf_path(account)
    for i in range(50):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            die("アカウント %s の設定が無い（box login %s）" % (account, account))
        except PermissionError:
            # Windows では、別のプロセスが置き換えている最中のファイルを開けないことがある
            if os.name != "nt" or i == 49:
                raise
            time.sleep(0.1)


def save(account, data):
    """一時ファイルに書いてから rename する。途中で落ちても元のファイルは壊れない。"""
    os.makedirs(CONF_DIR, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CONF_DIR, prefix="." + account + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        replace(tmp, conf_path(account))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def replace(src, dst):
    """os.replace。Windows では、別のプロセスが dst を開いている間は失敗するので、少し待ってやり直す。"""
    for i in range(50):
        try:
            return os.replace(src, dst)
        except PermissionError:
            if os.name != "nt" or i == 49:
                raise
            time.sleep(0.1)


class AccountLock:
    """アカウントごとの排他。トークンの更新を1プロセスずつにする。
    ロックはカーネルが持ち、持ち主のプロセスが終われば外れるので、残骸で詰まらない。
    macOS・Linux は flock、Windows は msvcrt.locking（ファイルの先頭1バイト）で取る。"""

    def __init__(self, account):
        os.makedirs(CONF_DIR, mode=0o700, exist_ok=True)
        self.path = os.path.join(CONF_DIR, "." + account + ".lock")

    def __enter__(self):
        self.f = open(self.path, "a+")
        if os.name == "nt":
            self.f.seek(0)
            while True:
                try:
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError as e:  # LK_LOCK は約10秒で諦めるので、取れるまで繰り返す
                    if e.errno not in (errno.EDEADLOCK, errno.EACCES):
                        raise
        else:
            fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        if os.name == "nt":
            self.f.seek(0)
            msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


# ---- HTTP ---------------------------------------------------------------------

_SSL_CONTEXT = None
_MAC_KEYCHAINS = ("/System/Library/Keychains/SystemRootCertificates.keychain",
                  "/Library/Keychains/System.keychain")


def load_pem_bundle(ctx, pem):
    """PEM の束を1件ずつ読む。読めない1件があっても残りは読む。"""
    begin, end = "-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----"
    for block in pem.split(end):
        i = block.find(begin)
        if i < 0:
            continue
        try:
            ctx.load_verify_locations(cadata=block[i:] + end + "\n")
        except (ssl.SSLError, ValueError):
            pass


def ssl_context():
    """OS が信頼する証明書で検証する文脈を作る。

    TLS を再署名する網（病院の Cisco Umbrella など）では、再署名に使う root CA を
    MDM が OS の証明書ストアへ入れている。Windows の Python は create_default_context で
    OS のストアを読むので通るが、macOS の Python（python.org 版・uv 版）は キーチェーンを
    読まず、自前の束（/etc/ssl/cert.pem や certifi）だけで検証して落ちる（2026-11-26、
    311C4M005 で CERTIFICATE_VERIFY_FAILED）。macOS ではシステムのキーチェーンの証明書を
    足す。Node 向けに NODE_EXTRA_CA_CERTS を置いた端末では、それも足す。
    """
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT
    ctx = ssl.create_default_context()
    if sys.platform == "darwin" and os.path.exists("/usr/bin/security"):
        try:
            r = subprocess.run(["/usr/bin/security", "find-certificate", "-a", "-p"]
                               + [k for k in _MAC_KEYCHAINS if os.path.exists(k)],
                               capture_output=True, text=True, timeout=60)
            load_pem_bundle(ctx, r.stdout)
        except (OSError, subprocess.SubprocessError):
            pass
    extra = os.environ.get("NODE_EXTRA_CA_CERTS")
    if extra and os.path.isfile(extra):
        try:
            with open(extra, encoding="utf-8", errors="replace") as f:
                load_pem_bundle(ctx, f.read())
        except OSError:
            pass
    _SSL_CONTEXT = ctx
    return ctx


def http_request(method, url, headers=None, body=None, allow_redirect=True):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    handlers = [urllib.request.HTTPSHandler(context=ssl_context())]
    if not allow_redirect:
        handlers.append(NoRedirect)
    opener = urllib.request.build_opener(*handlers)
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
            die("アカウント %s はログインしていない（box login %s）" % (account, account))
        try:
            tok = token_request({
                "grant_type": "refresh_token",
                "refresh_token": conf["refresh_token"],
                "client_id": conf["client_id"],
                "client_secret": conf["client_secret"],
            })
        except RuntimeError as e:
            die("%s。60日使わないと失効する。box login %s で入り直す" % (e, account))
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
            self.wfile.write("box: 受け取りました。このタブは閉じてよい。".encode())

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
    replace(tmp, dest)
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


def upload_file(account, folder_id, path, name=None, overwrite=False):
    """フォルダへ上げる。同じ名前があれば、overwrite なら新しい版として上げ、でなければ止まる。"""
    if os.path.getsize(path) > 50 * 1024 * 1024:
        die("50MB を超えるファイルは分割アップロードが要る（未対応）: " + path)
    name = name or os.path.basename(path)
    h, body = multipart({"name": name, "parent": {"id": str(folder_id)}}, path)
    st, _, data = call(account, "POST", UPLOAD + "/files/content", h, body)
    if st == 409 and overwrite:
        conflict = json.loads(data).get("context_info", {}).get("conflicts", {})
        if conflict.get("type") == "file":
            return upload_version(account, conflict["id"], path)
    check(st, data)
    return json.loads(data)["entries"][0]


def upload_version(account, file_id, path):
    if os.path.getsize(path) > 50 * 1024 * 1024:
        die("50MB を超えるファイルは分割アップロードが要る（未対応）: " + path)
    h, body = multipart({"name": os.path.basename(path)}, path)
    st, _, data = call(account, "POST", UPLOAD + "/files/%s/content" % file_id, h, body)
    check(st, data)
    return json.loads(data)["entries"][0]


def make_folder(account, parent_id, name, exist_ok=False):
    body = json.dumps({"name": name, "parent": {"id": str(parent_id)}}).encode()
    st, _, data = call(account, "POST", api_url("folders", None), {"Content-Type": "application/json"}, body)
    if st == 409 and exist_ok:
        conflict = json.loads(data).get("context_info", {}).get("conflicts", [])
        conflict = conflict[0] if isinstance(conflict, list) and conflict else conflict
        if isinstance(conflict, dict) and conflict.get("type") == "folder":
            return conflict
    check(st, data)
    return json.loads(data)


def list_items(account, folder_id, fields):
    entries, offset = [], 0
    while True:
        q = [("fields", fields), ("limit", "1000"), ("offset", str(offset))]
        st, _, data = call(account, "GET", api_url("folders/%s/items" % folder_id, q))
        check(st, data)
        page = json.loads(data)
        got = page.get("entries", [])
        entries.extend(got)
        offset += len(got)
        if not got or offset >= page.get("total_count", 0):
            return entries


def out(obj):
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


def cmd_upload(a):
    out(upload_file(a.account, a.folder_id, a.src, a.name, a.overwrite))


def cmd_upload_version(a):
    out(upload_version(a.account, a.file_id, a.src))


def cmd_items(a):
    out(list_items(a.account, a.folder_id, a.fields))


def cmd_mkdir(a):
    out(make_folder(a.account, a.parent_id, a.name, a.exist_ok))


def cmd_upload_dir(a):
    """手元のフォルダを、同じ名前のフォルダとして parent の下へ丸ごと上げる。"""
    def walk(local, parent):
        folder = make_folder(a.account, parent, os.path.basename(local.rstrip("/")), exist_ok=True)
        have = {e["name"]: e for e in list_items(a.account, folder["id"], "type,id,name")}
        for name in sorted(os.listdir(local)):
            if name.startswith("."):
                continue
            path = os.path.join(local, name)
            if os.path.isdir(path):
                walk(path, folder["id"])
            elif name in have and not a.overwrite:
                continue
            else:
                upload_file(a.account, folder["id"], path, name, a.overwrite)
        return folder
    out(walk(a.src, a.parent_id))


def main(argv):
    # 出力はどの OS でも UTF-8 にそろえる（Windows の既定は cp932 などで、日本語の名前を含む JSON が化けるか落ちる）
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    if argv[0] == "accounts":
        return cmd_accounts(None)
    if argv[0] == "login":
        p = argparse.ArgumentParser(prog="box login")
        p.add_argument("account")
        p.add_argument("--client-id")
        p.add_argument("--port", type=int)
        p.add_argument("--no-browser", action="store_true")
        return cmd_login(p.parse_args(argv[1:]))
    account, rest = argv[0], argv[1:]
    if not rest:
        die("コマンドが無い（box --help）", 2)
    cmd = rest[0]
    p = argparse.ArgumentParser(prog="box %s %s" % (account, cmd))
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
        p.add_argument("--overwrite", action="store_true")
        a = p.parse_args(rest[1:]); a.account = account
        return cmd_upload(a)
    if cmd == "items":
        p.add_argument("folder_id"); p.add_argument("--fields", default="type,id,name")
        a = p.parse_args(rest[1:]); a.account = account
        return cmd_items(a)
    if cmd == "mkdir":
        p.add_argument("parent_id"); p.add_argument("name"); p.add_argument("--exist-ok", action="store_true")
        a = p.parse_args(rest[1:]); a.account = account
        return cmd_mkdir(a)
    if cmd == "upload-dir":
        p.add_argument("parent_id"); p.add_argument("src"); p.add_argument("--overwrite", action="store_true")
        a = p.parse_args(rest[1:]); a.account = account
        return cmd_upload_dir(a)
    if cmd == "upload-version":
        p.add_argument("file_id"); p.add_argument("src")
        a = p.parse_args(rest[1:]); a.account = account
        return cmd_upload_version(a)
    die("知らないコマンド: %s（box --help）" % cmd, 2)


if __name__ == "__main__":
    main(sys.argv[1:])
