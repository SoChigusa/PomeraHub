# PomeraHub

## local tests

Install requirements

```bash
pip install -r requirements.txt
```

Prepare the environment variables in `.env` at the project root

```.env
GITHUB_TOKEN=<YOUR GITHUB FINE-GRAINED PAT>
GITHUB_OWNER=<YOUR GITHUB NAME>
GITHUB_REPO=<REPOSITORY NAME>
ALLOWED_SENDERS=<GMAIL ADDRESS>
DEFAULT_BRANCH=main
GMAIL_WEBHOOK_TOKEN=<TOKEN HERE>
```

and the test payload `payload.json`

```json
{
  "from": "YOU <GMAIL ADDRESS>",
  "to": "<GMAIL ADDRESS>",
  "subject": "test",
  "body_plain": "Hello from PomeraHub",
  "body_html": "",
  "message_id": "<manual-test-1@local>",
  "branch": "main"
}
```

Launch the server

```bash
uvicorn app:app --reload --port 8080
```

and invoke the webhook

```bash
curl -X POST http://127.0.0.1:8080/gmail/inbound   -H "Content-Type: application/json"   -H "X-Webhook-Token: <TOKEN HERE>"   --data-binary @payload.json"
```