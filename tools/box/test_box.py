#!/usr/bin/env python3
"""box.py の検証。本物の Box には触れず、手元に立てた偽の Box サーバーで確かめる。

  python3 tools/box/test_box.py

確かめること: ログイン（認可コード）、トークンの自動更新、更新用トークンの使い捨て（rotation）の下で
8プロセスが同時に更新しても1回だけ更新されること、401 での更新とやり直し、429 の待ちとやり直し、
一覧の全ページ読み（offset と marker）、ダウンロード（リダイレクト）、アップロード。
"""
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "box.py")


class State:
    lock = threading.Lock()
    refresh = "r0"          # 今有効な更新用トークン（使うと無効になる）
    access = set()          # 有効なアクセストークン
    n_refresh = 0
    n_429 = 0
    fs = {"500": {"name": "root", "items": []}}   # フォルダ id → 中身（type,id,name）
    next_id = 1000


class Mock(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj=None, headers=None, raw=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        body = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else b"")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        tok = (self.headers.get("Authorization") or "")[7:]
        if tok not in State.access:
            self.send(401, {"code": "unauthorized"})
            return False
        return True

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        u = urllib.parse.urlparse(self.path)
        if u.path == "/oauth2/token":
            f = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
            if f.get("client_id") != "cid" or f.get("client_secret") != "csecret":
                return self.send(400, {"error": "invalid_client"})
            with State.lock:
                if f["grant_type"] == "authorization_code":
                    if f.get("code") != "c1":
                        return self.send(400, {"error": "invalid_grant"})
                elif f["grant_type"] == "refresh_token":
                    if f.get("refresh_token") != State.refresh:
                        return self.send(400, {"error": "invalid_grant", "error_description": "Refresh token has expired"})
                    State.n_refresh += 1
                time.sleep(0.2)  # 更新が重なりやすいように遅らせる
                State.refresh = "r%d" % (State.n_refresh + 100 + len(State.access))
                at = "a%d" % (len(State.access) + 1)
                State.access.add(at)
                return self.send(200, {"access_token": at, "refresh_token": State.refresh, "expires_in": 3600})
        if u.path == "/upload/files/content":
            if not self.authed():
                return
            m = re.search(rb'name="attributes"\r\n\r\n(.*?)\r\n', body, re.S)
            attrs = json.loads(m.group(1))
            pid = attrs["parent"]["id"]
            if pid in State.fs:
                same = [e for e in State.fs[pid]["items"] if e["name"] == attrs["name"]]
                if same:
                    return self.send(409, {"code": "item_name_in_use", "context_info": {"conflicts": dict(same[0], type="file")}})
                State.next_id += 1
                e = {"type": "file", "id": str(State.next_id), "name": attrs["name"], "version": 1}
                State.fs[pid]["items"].append(e)
                return self.send(201, {"entries": [e]})
            return self.send(201, {"entries": [{"type": "file", "id": "9", "name": attrs["name"], "size": len(body)}]})
        m = re.match(r"/upload/files/(\d+)/content$", u.path)
        if m:
            if not self.authed():
                return
            for f in State.fs.values():
                for e in f["items"]:
                    if e["id"] == m.group(1):
                        e["version"] += 1
                        return self.send(201, {"entries": [e]})
            return self.send(404, {})
        if u.path == "/2.0/folders":
            if not self.authed():
                return
            req = json.loads(body)
            pid = req["parent"]["id"]
            same = [e for e in State.fs[pid]["items"] if e["name"] == req["name"]]
            if same:
                return self.send(409, {"code": "item_name_in_use", "context_info": {"conflicts": [same[0]]}})
            State.next_id += 1
            e = {"type": "folder", "id": str(State.next_id), "name": req["name"]}
            State.fs[pid]["items"].append(e)
            State.fs[e["id"]] = {"name": req["name"], "items": []}
            return self.send(201, e)
        self.send(404, {})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/dl/1":
            return self.send(200, raw=b"FILE-BYTES")
        if not self.authed():
            return
        if u.path == "/2.0/users/me":
            return self.send(200, {"login": "me@example.org", "name": "Me", "role": "admin"})
        if u.path == "/2.0/flaky":
            with State.lock:
                State.n_429 += 1
                first = State.n_429 == 1
            if first:
                return self.send(429, {}, {"Retry-After": "1"})
            return self.send(200, {"ok": True})
        m = re.match(r"/2.0/folders/(\d+)/items$", u.path)
        if m and m.group(1) in State.fs:
            items = State.fs[m.group(1)]["items"]
            off = int(q.get("offset", 0))
            return self.send(200, {"entries": items[off:off + int(q.get("limit", 100))], "total_count": len(items)})
        if u.path == "/2.0/folders/0/items":
            total, limit = 250, int(q.get("limit", 100))
            if q.get("usemarker"):
                start = int(q.get("marker") or 0)
                ents = [{"id": str(i)} for i in range(start, min(start + limit, total))]
                nxt = str(start + limit) if start + limit < total else None
                return self.send(200, {"entries": ents, "next_marker": nxt})
            off = int(q.get("offset", 0))
            ents = [{"id": str(i)} for i in range(off, min(off + limit, total))]
            return self.send(200, {"entries": ents, "total_count": total, "offset": off})
        if u.path == "/2.0/files/1":
            return self.send(200, {"name": "report.pdf"})
        if u.path == "/2.0/files/1/content":
            return self.send(302, None, {"Location": "http://127.0.0.1:%d/dl/1" % PORT})
        self.send(404, {})


def run(args, env, inp=None):
    return subprocess.run([sys.executable, TOOL] + args, env=env, input=inp, capture_output=True, text=True, encoding="utf-8", timeout=60)


def main():
    global PORT
    sys.stdout.reconfigure(encoding="utf-8")  # Windows の既定（cp932 など）でも結果の行を化けさせない
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % PORT
    conf = tempfile.mkdtemp()
    env = dict(os.environ, BOX_CONFIG_DIR=conf, BOX_API_BASE=base + "/2.0", BOX_UPLOAD_BASE=base + "/upload",
               BOX_OAUTH_BASE=base + "/oauth2", BOX_AUTHORIZE_URL=base + "/authorize")
    fails = []

    def ok(name, cond, detail=""):
        print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  " + detail))
        if not cond:
            fails.append(name)

    # 1 ログイン：ブラウザの代わりに、表示された URL の redirect_uri へ code と state を返す
    p = subprocess.Popen([sys.executable, TOOL, "login", "t", "--client-id", "cid", "--port", "18765", "--no-browser"],
                         env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    p.stdin.write("csecret\n"); p.stdin.flush()
    url = None
    for _ in range(50):
        line = p.stderr.readline()
        if line.startswith("http"):
            url = line.strip(); break
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    cb = qs["redirect_uri"][0] + "?" + urllib.parse.urlencode({"code": "c1", "state": qs["state"][0]})
    time.sleep(0.3)
    urllib.request.urlopen(cb).read()
    out, err = p.communicate(timeout=30)
    ok("login", p.returncode == 0 and "me@example.org" in out, out + err)
    if os.name != "nt":  # Windows の権限は chmod では決まらない（ユーザーのプロファイルの下で守る）
        mode = oct(os.stat(os.path.join(conf, "t.json")).st_mode & 0o777)
        ok("設定ファイルの権限が 600", mode == "0o600", mode)

    def expire():
        c = json.load(open(os.path.join(conf, "t.json"), encoding="utf-8"))
        c["expires_at"] = 0
        json.dump(c, open(os.path.join(conf, "t.json"), "w", encoding="utf-8"))

    # 2 期限切れなら自動で更新する
    expire()
    r = run(["t", "whoami"], env)
    ok("期限切れで自動更新", r.returncode == 0 and State.n_refresh == 1, r.stderr)

    # 3 8プロセスが同時に更新しても、更新は1回だけで全員が通る（更新用トークンは使い捨て）
    expire()
    before = State.n_refresh
    ps = [subprocess.Popen([sys.executable, TOOL, "t", "whoami"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8") for _ in range(8)]
    rcs = [q.wait(timeout=60) for q in ps]
    ok("同時8プロセスで更新は1回・全員成功", rcs == [0] * 8 and State.n_refresh == before + 1,
       "rcs=%s refresh=%d" % (rcs, State.n_refresh - before))

    # 4 アクセストークンが無効（期限内）なら 401 を受けて更新し、やり直す
    c = json.load(open(os.path.join(conf, "t.json"), encoding="utf-8"))
    c["access_token"] = "bogus"; c["refreshed_at"] = 0
    json.dump(c, open(os.path.join(conf, "t.json"), "w", encoding="utf-8"))
    r = run(["t", "whoami"], env)
    ok("401 で更新してやり直す", r.returncode == 0, r.stderr)

    # 5 429 は待ってやり直す
    r = run(["t", "get", "flaky"], env)
    ok("429 を待ってやり直す", r.returncode == 0 and State.n_429 == 2, r.stderr)

    # 6 一覧の全ページ（offset と marker）
    r = run(["t", "get", "folders/0/items", "--query", "limit=100", "--all"], env)
    ok("--all（offset）で 250 件", r.returncode == 0 and json.loads(r.stdout)["total_count"] == 250, r.stderr)
    r = run(["t", "get", "folders/0/items", "--query", "limit=100", "--query", "usemarker=true", "--all"], env)
    ok("--all（marker）で 250 件", r.returncode == 0 and json.loads(r.stdout)["total_count"] == 250, r.stderr)

    # 7 ダウンロード（302 の先から本体を取る。保存先がディレクトリなら元の名前）
    d = tempfile.mkdtemp()
    r = run(["t", "download", "1", d], env)
    path = os.path.join(d, "report.pdf")
    ok("ダウンロード", r.returncode == 0 and os.path.exists(path) and open(path, "rb").read() == b"FILE-BYTES", r.stderr)

    # 8 アップロード（日本語のファイル名）
    src = os.path.join(d, "資料.txt")
    open(src, "w", encoding="utf-8").write("hello")
    r = run(["t", "upload", "123", src], env)
    ok("アップロード（日本語の名前が届く）", r.returncode == 0 and json.loads(r.stdout)["name"] == "資料.txt", r.stdout + r.stderr)

    # 8b 同じ名前：--overwrite なしは止まり、ありなら新しい版になる
    r1 = run(["t", "upload", "500", src], env)
    r2 = run(["t", "upload", "500", src], env)
    r3 = run(["t", "upload", "500", src, "--overwrite"], env)
    ok("同名は止まり、--overwrite で新しい版", r1.returncode == 0 and r2.returncode != 0 and r3.returncode == 0 and json.loads(r3.stdout)["version"] == 2,
       r2.stderr + r3.stdout + r3.stderr)

    # 8c mkdir と --exist-ok、items
    a1 = run(["t", "mkdir", "500", "回のフォルダ"], env)
    a2 = run(["t", "mkdir", "500", "回のフォルダ"], env)
    a3 = run(["t", "mkdir", "500", "回のフォルダ", "--exist-ok"], env)
    ok("mkdir（既存は止まり、--exist-ok で同じ id）", a1.returncode == 0 and a2.returncode != 0 and a3.returncode == 0
       and json.loads(a1.stdout)["id"] == json.loads(a3.stdout)["id"], a2.stderr + a3.stderr)
    it = run(["t", "items", "500"], env)
    names = sorted(e["name"] for e in json.loads(it.stdout))
    ok("items で全件を配列で", it.returncode == 0 and names == ["回のフォルダ", "資料.txt"], it.stdout + it.stderr)

    # 8d upload-dir：入れ子のフォルダを丸ごと。2回目は既存を飛ばす
    tree = os.path.join(d, "審査資料")
    os.makedirs(os.path.join(tree, "添付"))
    open(os.path.join(tree, "a.pdf"), "w").write("a")
    open(os.path.join(tree, "添付", "b.pdf"), "w").write("b")
    u1 = run(["t", "upload-dir", "500", tree], env)
    u2 = run(["t", "upload-dir", "500", tree], env)
    top = json.loads(u1.stdout)["id"] if u1.returncode == 0 else None
    sub = [e for e in State.fs.get(top, {"items": []})["items"] if e["type"] == "folder"]
    ok("upload-dir（入れ子、2回目は既存を飛ばす）", u1.returncode == 0 and u2.returncode == 0 and len(State.fs[top]["items"]) == 2
       and sub and [e["name"] for e in State.fs[sub[0]["id"]]["items"]] == ["b.pdf"], u1.stderr + u2.stderr)

    # 9 失効した更新用トークンでは、入り直すよう案内して止まる
    c = json.load(open(os.path.join(conf, "t.json"), encoding="utf-8"))
    c["expires_at"] = 0; c["refresh_token"] = "dead"
    json.dump(c, open(os.path.join(conf, "t.json"), "w", encoding="utf-8"))
    r = run(["t", "whoami"], env)
    ok("失効した更新用トークンで止まる", r.returncode != 0 and "login" in r.stderr, r.stderr)

    # 10 設定の無いアカウント
    r = run(["nobody", "whoami"], env)
    ok("設定の無いアカウントで止まる", r.returncode != 0 and "login" in r.stderr, r.stderr)

    print("\n%d 件失敗" % len(fails) if fails else "\nすべて通った")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
