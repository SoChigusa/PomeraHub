# PomeraHub

This project is to set up an environment where sending an email with text contents automatically makes a new commit and push contents to a GitHub repository

## Set up

- Prepare a github repository for text upload. Create and save a fine-grained PAT with read & write permissions

- Prepare an arbitrary token used as a webhook token (referred to as `<TOKEN HERE>` below)

- Prepare a Google account to receive emails

- Folk and deploy the current project to a service like "Render"

- Set the following environment variables

```.env
GITHUB_TOKEN=<GITHUB FINE-GRAINED PAT>
GITHUB_OWNER=<GITHUB USERNAME>
GITHUB_REPO=<REPOSITORY NAME>
ALLOWED_SENDERS=<GMAIL ADDRESS>
DEFAULT_BRANCH=main
GMAIL_WEBHOOK_TOKEN=<TOKEN HERE>
```

- Log in to the Google account and create a Google Apps Script (GAS). Copy `GAS/code.gs` and replace `<DEPLOY_URL>`, `<FROM ADDRESS>`, and `<TOKEN HERE>`. Run `installTrigger()` to set a trigger

## How to use

- Send an email from `<FROM ADDRESS>` to `<GMAIL ADDRESS>`

- The title of the email specifies the filename on the GitHub repository

  - The extension `.md` is automatically attached
  - `￥` is recognized as the directory separator, which can be used to make directories and specify locations
  - Add `[append]` to update with the append-mode, otherwise the file is overwritten if exist

- The main text of the email specifies the content of the file

### For Pomera users

(As of 2025/8/31)

When gmail is used to upload files from Pomera, we need to work in advance with the Google account

- to [activate the two-factor authorization](https://support.google.com/accounts/answer/185839)

- to [generate an application password](https://support.google.com/accounts/answer/185833)

Use the application password instead of the normally used one to configure the "upload" button of Pomera

## [Development memo] Local tests

Install requirements

```bash
pip install -r requirements.txt
```

Prepare the environment variables in `.env` at the project root

```.env
GITHUB_TOKEN=<GITHUB FINE-GRAINED PAT>
GITHUB_OWNER=<GITHUB USERNAME>
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