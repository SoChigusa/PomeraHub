# app.py
import os
import re
import base64
import hashlib
from datetime import datetime, timezone
from html import unescape

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
from email.utils import parseaddr

# --- .env 読み込み ---
from dotenv import load_dotenv
load_dotenv()  # プロジェクトルートの .env を読み込む

# --- 環境変数 ---
GITHUB_TOKEN        = os.environ["GITHUB_TOKEN"]         # Fine-grained PAT（Contents: RW）
GITHUB_OWNER        = os.environ["GITHUB_OWNER"]
GITHUB_REPO         = os.environ["GITHUB_REPO"]
DEFAULT_BRANCH      = os.environ.get("DEFAULT_BRANCH", "main")
ALLOWED_SENDERS_RAW = os.environ.get("ALLOWED_SENDERS", "")  # "you@ex.com,example.org" のようにカンマ区切り
ALLOWED_SENDERS     = [x.strip().lower() for x in ALLOWED_SENDERS_RAW.split(",") if x.strip()]

# ★ 共有トークン（Apps Script から 'X-Webhook-Token' ヘッダで送る）
GMAIL_WEBHOOK_TOKEN = os.environ["GMAIL_WEBHOOK_TOKEN"]

GITHUB_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"

# html2text は optional
try:
    import html2text
    h2t = html2text.HTML2Text()
    h2t.ignore_images = True
    h2t.ignore_links = False
except Exception:
    h2t = None

app = FastAPI(title="mail2git (Gmail webhook)")

# ---------------- ユーティリティ ----------------

def sender_allowed(sender: str) -> bool:
    """
    ALLOWED_SENDERS:
      - 空（未設定）なら全許可
      - メールアドレス（exact match）
      - ドメイン（example.com のように @無し）
    """
    if not ALLOWED_SENDERS:
        return True

    s = (sender or "").strip().lower()

    # 1) メールアドレス本体を抽出
    _, addr = parseaddr(s)  # "Name <a@b>" -> ("Name", "a@b")
    addr = addr.lower()

    # 2) ドメインも抽出
    m = re.search(r'@([^>]+)$', addr)
    dom = m.group(1) if m else ""

    # 3) 許可判定（アドレス一致 or ドメイン一致）
    for allowed in ALLOWED_SENDERS:
        a = allowed.strip().lower()
        if not a:
            continue
        if '@' in a:
            if addr == a:
                return True
        else:
            # ドメイン（@無し）での一致
            if dom == a:
                return True

    return False

def sanitize_path(subject: str) -> str:
    """件名→安全な相対パス。拡張子がなければ .md を付与"""
    subject = (subject or "").strip()
    # [append] フラグは別処理なので除去（判定は呼び出し側でやる）
    subject = re.sub(r'^\[append\]\s*', '', subject, flags=re.I)

    # パスサニタイズ
    subj = subject.replace("\\", "/")
    subj = re.sub(r"\s+", " ", subj)
    parts = [p for p in subj.split("/") if p not in ("", ".", "..")]
    path = "/".join(parts)

    if not path:
        # 件名が空なら自動命名
        path = datetime.now(timezone.utc).strftime("notes/%Y-%m-%d-%H%M%S.md")

    # 拡張子が無ければ .md
    if not re.search(r"\.[A-Za-z0-9]{1,6}$", path):
        path += ".md"

    return path

def html_to_markdown(html: str) -> str:
    if not html:
        return ""
    if h2t:
        return h2t.handle(html)
    # 簡易フォールバック
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text)

async def github_get_file(path: str, branch: str):
    """
    GitHub: 既存ファイルの SHA と テキスト内容を取得
    戻り値: (sha or None, text or "")
    """
    url = f"{GITHUB_API}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.githubjson"}
    params = {"ref": branch}
    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        r = await client.get(url, params=params)
        if r.status_code == 200:
            j = r.json()
            sha = j.get("sha")
            content_b64 = j.get("content", "")
            # APIは末尾に改行とbase64の改行が含まれる場合があるのでstripしない
            try:
                text = base64.b64decode(content_b64.encode("ascii")).decode("utf-8")
            except Exception:
                text = ""
            return sha, text
        if r.status_code == 404:
            return None, ""
        raise HTTPException(r.status_code, f"GitHub get file error: {r.text}")

async def github_put_file(path: str, content_text: str, message: str, branch: str, sha: str | None):
    """GitHub: ファイル作成/更新"""
    url = f"{GITHUB_API}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    b64 = base64.b64encode(content_text.encode("utf-8")).decode("ascii")
    data = {"message": message, "content": b64, "branch": branch}
    if sha:
        data["sha"] = sha
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        r = await client.put(url, json=data)
        if r.status_code >= 300:
            raise HTTPException(r.status_code, f"GitHub put error: {r.text}")
        return r.json()

async def append_text_from_repo(path: str, branch: str) -> str:
    """Raw URL から既存テキストを取得（append 用）"""
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{branch}/{path}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(raw_url)
        if r.status_code == 200:
            return r.text
        return ""

# ---------------- エンドポイント ----------------

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/gmail/inbound")
async def gmail_inbound(request: Request):
    """
    Apps Script から JSON を受ける入口。
    共有トークンを 'X-Webhook-Token' で検証し、件名→パス、本文→ファイル内容として GitHub にコミット。
    期待する JSON:
    {
      "from": "Alice <alice@example.com>",
      "to": "notes@yourdomain",
      "subject": "docs/hello.md",
      "body_plain": "Hello",
      "body_html": "<p>Hello</p>",
      "message_id": "<abcd@mail.gmail.com>",
      "branch": "main"            # 任意（無ければ DEFAULT_BRANCH）
    }
    """
    # --- 共有トークン検証 ---
    token = request.headers.get("x-webhook-token")
    if not token or token != GMAIL_WEBHOOK_TOKEN:
        raise HTTPException(403, "Invalid webhook token")

    # --- JSON 受取 ---
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    sender   = (data.get("from") or "").strip()
    recipient= (data.get("to") or "").strip()
    subject  = (data.get("subject") or "").strip()
    body_p   = (data.get("body_plain") or "").rstrip()
    body_h   = (data.get("body_html") or "").strip()
    msg_id   = (data.get("message_id") or "").strip()
    branch   = (data.get("branch") or DEFAULT_BRANCH).strip() or DEFAULT_BRANCH

    # --- 送信者チェック（許可リスト） ---
    if not sender_allowed(sender):
        raise HTTPException(403, f"Sender not allowed: {sender}")

    # --- append モード判定 & パス決定 ---
    append_mode = bool(re.match(r'^\[append\]\s*', subject, flags=re.I))
    path = sanitize_path(subject)

    # --- 本文抽出（text/plain 優先、無ければ HTML -> MD）---
    content = body_p if body_p else html_to_markdown(body_h)
    if not content:
        content = "(empty)"

    # --- 付加メタデータ（任意）---
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = (
        "\n\n---\n"
        f"Received: {now_utc}\n"
        f"From: {sender}\n"
        # f"To: {recipient}\n"
        # f"Message-Id: {msg_id or '(none)'}\n"
    )

    # --- 既存 SHA 取得 ---
    sha, base_text = await github_get_file(path, branch)

    # --- 内容確定（追記 or 上書き/新規）---
    if append_mode and sha:
        new_text = base_text.rstrip() + "\n\n" + content + meta + "\n"
        commit_msg = f"PomeraHub(append): {path} @ {now_utc}\n\nMessage-Id: {msg_id}"
        res = await github_put_file(path, new_text, commit_msg, branch, sha)
    else:
        text = content + meta + "\n"
        # 既存があれば上書き（sha 指定）、無ければ新規
        commit_msg = f"PomeraHub: {path} @ {now_utc}\n\nMessage-Id: {msg_id}"
        res = await github_put_file(path, text, commit_msg, branch, sha)

    commit_sha = (res.get("commit") or {}).get("sha")
    return JSONResponse({"status": "ok", "path": path, "branch": branch, "commit": commit_sha})
