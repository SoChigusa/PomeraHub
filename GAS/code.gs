const WEBHOOK_URL = '<DEPLOY_URL>/gmail/inbound';
const ALLOW_FROM = [<FROM ADDRESS>];

function installTrigger() {
  ScriptApp.newTrigger('scanInbox').timeBased().everyMinutes(5).create();
}

function scanInbox() {
  const labelName = 'processed_by_PomeraHub';
  const processedLabel = getOrCreateLabel_(labelName);
  const query = 'in:inbox newer_than:10m -label:' + labelName;
  const threads = GmailApp.search(query, 0, 30);

  threads.forEach(thread => {

    // skip when processed
    const hasProcessed = thread.getLabels().some(l => l.getName() === labelName);
    if (hasProcessed) return;

    // most recent in the thread
    const messages = thread.getMessages();
    const msg = messages[messages.length - 1];

    // filter by sender
    const from = msg.getFrom();
    if (ALLOW_FROM.length && !isAllowed_(from)) return;

    // Trim Re:/Fwd:
    let subject = (msg.getSubject() || '').replace(/^\s*(Re|Fwd):\s*/ig, '').trim();

    const payload = {
      from: from,
      to: msg.getTo(),
      subject: subject,
      body_plain: msg.getPlainBody() || '',
      body_html: msg.getBody() || '',
      message_id: extractMessageId_(msg) || ('gmail-' + msg.getId()),
    };

    // Webhook POST
    const res = UrlFetchApp.fetch(WEBHOOK_URL, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify(payload),
      headers: { 'X-Webhook-Token': '<TOKEN HERE>' },
      muteHttpExceptions: true
    });

    // processed label when succeed
    const code = res.getResponseCode();
    if (code >= 200 && code < 300) {
      thread.addLabel(processedLabel);
    } else {
      console.log('Webhook failed:', code, res.getContentText());
    }
  });
}

function getOrCreateLabel_(name) {
  return GmailApp.getUserLabelByName(name) || GmailApp.createLabel(name);
}

function isAllowed_(from) {
  const f = (from || '').toLowerCase();
  return ALLOW_FROM.some(x => f.includes(x.toLowerCase()));
}

function extractMessageId_(msg) {
  const raw = msg.getRawContent() || '';
  const m = raw.match(/Message-Id:\s*(<[^>]+>)/i);
  return m ? m[1].trim() : null;
}
