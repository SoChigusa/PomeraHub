const WEBHOOK_URL = '<DEPLOY_URL>/gmail/inbound';
const ALLOW_FROM = [<FROM ADDRESS>]; // 任意で制限

function installTrigger() {
  ScriptApp.newTrigger('scanInbox').timeBased().everyMinutes(5).create();
}

function scanInbox() {
  const labelName = 'processed_by_PomeraHub';
  const processedLabel = getOrCreateLabel_(labelName);

  // 受信トレイに来た新着のみ。すでに処理済みラベルが付いたスレッドは除外
  const query = 'in:inbox newer_than:10m -label:' + labelName;
  const threads = GmailApp.search(query, 0, 30);

  threads.forEach(thread => {
    // すでにスレッドに処理済みラベルが付いていればスキップ
    const hasProcessed = thread.getLabels().some(l => l.getName() === labelName);
    if (hasProcessed) return;

    // スレッドの「最新1通のみ」を処理（多重POST防止＆“全部投げ”防止）
    const messages = thread.getMessages();
    const msg = messages[messages.length - 1];

    // 送信者フィルタ
    const from = msg.getFrom();
    if (ALLOW_FROM.length && !isAllowed_(from)) return;

    // 件名を整形：先頭の Re:/Fwd: を除去して [append] 判定が効くようにする
    let subject = (msg.getSubject() || '').replace(/^\s*(Re|Fwd):\s*/ig, '').trim();

    const payload = {
      from: from,
      to: msg.getTo(),
      subject: subject,                              // ← 件名＝ファイルパス（[append] 先頭OK）
      body_plain: msg.getPlainBody() || '',
      body_html: msg.getBody() || '',
      message_id: extractMessageId_(msg) || ('gmail-' + msg.getId()),
      // branch を固定したいならここで指定も可: branch: 'main'
    };

    // Webhook POST
    const res = UrlFetchApp.fetch(WEBHOOK_URL, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify(payload),
      headers: { 'X-Webhook-Token': '<TOKEN HERE>' },
      muteHttpExceptions: true
    });

    const code = res.getResponseCode();
    // 成功した時だけ“スレッド”に処理済みラベルを付ける（これが二重投稿防止の要）
    if (code >= 200 && code < 300) {
      thread.addLabel(processedLabel);
    } else {
      // 失敗時はラベリングしない → 次回リトライで拾える
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
  // RFC 5322 Message-ID を生ヘッダから抜く（あればベスト、無ければ null）
  const raw = msg.getRawContent() || '';
  const m = raw.match(/Message-Id:\s*(<[^>]+>)/i);
  return m ? m[1].trim() : null;
}
