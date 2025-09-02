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

# for local tests with .env
from dotenv import load_dotenv
load_dotenv()

# environment variables
GITHUB_TOKEN        = os.environ["GITHUB_TOKEN"]
GITHUB_OWNER        = os.environ["GITHUB_OWNER"]
GITHUB_REPO         = os.environ["GITHUB_REPO"]
DEFAULT_BRANCH      = os.environ.get("DEFAULT_BRANCH", "main")
ALLOWED_SENDERS_RAW = os.environ.get("ALLOWED_SENDERS", "")
ALLOWED_SENDERS     = [x.strip().lower() for x in ALLOWED_SENDERS_RAW.split(",") if x.strip()]
GMAIL_WEBHOOK_TOKEN = os.environ["GMAIL_WEBHOOK_TOKEN"]
GITHUB_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"

# optional
try:
    import html2text
    h2t = html2text.HTML2Text()
    h2t.ignore_images = True
    h2t.ignore_links = False
except Exception:
    h2t = None

app = FastAPI(title="mail2git (Gmail webhook)")

# ---------------- utilities ----------------

def sender_allowed(sender: str) -> bool:
    """
    ALLOWED_SENDERS:
      - allows everyone if empty
      - specify by email addresses（exact match）
      - or by domains（w/o @）
    """
    if not ALLOWED_SENDERS:
        return True

    s = (sender or "").strip().lower()

    # extract email address
    _, addr = parseaddr(s)  # "Name <a@b>" -> ("Name", "a@b")
    addr = addr.lower()

    # extract domain
    m = re.search(r'@([^>]+)$', addr)
    dom = m.group(1) if m else ""

    # judge allowed
    for allowed in ALLOWED_SENDERS:
        a = allowed.strip().lower()
        if not a:
            continue
        if '@' in a:
            if addr == a:
                return True
        else:
            if dom == a:
                return True

    return False

def sanitize_path(subject: str) -> str:
    """
    title 2 path
    - trim [append] at front
    - use "￥" and '/' as directory separators
    - automatic attachment '.md'
    """
    subject = (subject or "").strip()
    subject = re.sub(r'^\[append\]\s*', '', subject, flags=re.I)

    # directory separators
    normalized = subject.replace("￥", "/")
    normalized = re.sub(r'/+', '/', normalized)
    parts = [p.strip() for p in normalized.split('/') if p not in ("", ".", "..")]
    path = "/".join(parts)

    if not path:
        path = datetime.now(timezone.utc).strftime("notes/%Y-%m-%d-%H%M%S.md")

    if not re.search(r"\.[A-Za-z0-9]{1,6}$", path):
        path += ".md"

    return path

def html_to_markdown(html: str) -> str:
    if not html:
        return ""
    if h2t:
        return h2t.handle(html)
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text)

async def github_get_file(path: str, branch: str):
    """
    GitHub: SHA and content of existing files
    return (sha or None, text or "")
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
            try:
                text = base64.b64decode(content_b64.encode("ascii")).decode("utf-8")
            except Exception:
                text = ""
            return sha, text
        if r.status_code == 404:
            return None, ""
        raise HTTPException(r.status_code, f"GitHub get file error: {r.text}")

async def github_put_file(path: str, content_text: str, message: str, branch: str, sha: str | None):
    """GitHub: file creation/update"""
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
    """Raw URL 2 text (prepared for append)"""
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{branch}/{path}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(raw_url)
        if r.status_code == 200:
            return r.text
        return ""

# ---------------- end-point ----------------

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/gmail/inbound")
async def gmail_inbound(request: Request):
    """
    end-point for json submission from GAS
    expected JSON format:
    {
      "from": "Alice <alice@example.com>",
      "to": "notes@yourdomain",
      "subject": "docs/hello.md",
      "body_plain": "Hello",
      "body_html": "<p>Hello</p>",
      "message_id": "<abcd@mail.gmail.com>",
      "branch": "main" # optional
    }
    """
    # --- token ---
    token = request.headers.get("x-webhook-token")
    if not token or token != GMAIL_WEBHOOK_TOKEN:
        raise HTTPException(403, "Invalid webhook token")

    # --- receive JSON ---
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

    # --- judge allowed senders ---
    if not sender_allowed(sender):
        raise HTTPException(403, f"Sender not allowed: {sender}")

    # --- judge append + path ---
    append_mode = bool(re.match(r'^\[append\]\s*', subject, flags=re.I))
    path = sanitize_path(subject)

    # --- extract main text ---
    content = body_p if body_p else html_to_markdown(body_h)
    if not content:
        content = "(empty)"

    # --- meta data ---
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = (
        "\n\n---\n"
        f"Received: {now_utc}\n"
        f"From: {sender}\n"
        # f"To: {recipient}\n"
        # f"Message-Id: {msg_id or '(none)'}\n"
    )

    # --- existing SHA ---
    sha, base_text = await github_get_file(path, branch)

    # --- create, append, or overwrite ---
    if append_mode and sha:
        new_text = base_text.rstrip() + "\n\n" + content + meta + "\n"
        commit_msg = f"PomeraHub(append): {path} @ {now_utc}\n\nMessage-Id: {msg_id}"
        res = await github_put_file(path, new_text, commit_msg, branch, sha)
    else:
        text = content + meta + "\n"
        commit_msg = f"PomeraHub: {path} @ {now_utc}\n\nMessage-Id: {msg_id}"
        res = await github_put_file(path, text, commit_msg, branch, sha)

    commit_sha = (res.get("commit") or {}).get("sha")
    return JSONResponse({"status": "ok", "path": path, "branch": branch, "commit": commit_sha})
