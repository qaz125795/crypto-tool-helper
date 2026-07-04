'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const PORT = Number(process.env.PORT || 3000);
const HOST = process.env.HOSTNAME || '0.0.0.0';
const ROOT = __dirname;
const DATA_DIR = path.join(ROOT, '.data');
const STORE_PATH = path.join(DATA_DIR, 'uid_store.json');

const REVIEW_TOKEN = process.env.AGENT_REVIEW_TOKEN || '@Jacky87084';
const TG_TOKEN = process.env.ARENA_TG_TOKEN || '8941144271:AAEx0n2xY2gDiDewg1VglE1qvFRuwj7hBck';
const TG_CHATS = (process.env.ARENA_TG_CHATS || '6312951992').split(',').map(s => s.trim()).filter(Boolean);
const PUBLIC_BASE = (process.env.AGENT_VAULT_PUBLIC_BASE || 'https://108.160.139.47/war-room/apps/p-6e6dee8f/').replace(/\/?$/, '/');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
};

function ensureStore() {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
  if (!fs.existsSync(STORE_PATH)) {
    const seed = { pending: [], approved: [], audit: [] };
    fs.writeFileSync(STORE_PATH, JSON.stringify(seed, null, 2), 'utf8');
  }
}

function readStore() {
  ensureStore();
  try {
    const raw = fs.readFileSync(STORE_PATH, 'utf8');
    const data = JSON.parse(raw);
    data.pending = Array.isArray(data.pending) ? data.pending : [];
    data.approved = Array.isArray(data.approved) ? data.approved : [];
    data.audit = Array.isArray(data.audit) ? data.audit : [];
    return data;
  } catch (e) {
    return { pending: [], approved: [], audit: [] };
  }
}

function writeStore(data) {
  ensureStore();
  fs.writeFileSync(STORE_PATH, JSON.stringify(data, null, 2), 'utf8');
}

function json(res, code, data) {
  res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
  res.end(JSON.stringify(data));
}

function text(res, code, contentType, body) {
  res.writeHead(code, { 'content-type': contentType, 'cache-control': 'no-store' });
  res.end(body);
}

function parseBody(req) {
  return new Promise((resolve) => {
    let body = '';
    req.on('data', (chunk) => {
      body += chunk.toString('utf8');
      if (body.length > 1_000_000) req.destroy();
    });
    req.on('end', () => {
      if (!body) return resolve({});
      try { resolve(JSON.parse(body)); } catch (e) { resolve({}); }
    });
    req.on('error', () => resolve({}));
  });
}

function nowISO() {
  return new Date().toISOString();
}

function isValidUid(uid) {
  return /^[0-9]{5,20}$/.test(uid || '');
}

async function tgSend(textMsg, inlineKeyboard) {
  if (!TG_TOKEN || !TG_CHATS.length) return;
  const url = `https://api.telegram.org/bot${TG_TOKEN}/sendMessage`;
  for (const chatId of TG_CHATS) {
    const payload = {
      chat_id: chatId,
      text: textMsg,
      disable_web_page_preview: true,
    };
    if (inlineKeyboard) payload.reply_markup = { inline_keyboard: inlineKeyboard };
    try {
      await fetch(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (e) {}
  }
}

function approvedLookup(store, uid) {
  return store.approved.find((x) => x.uid === uid);
}

function pendingLookup(store, uid) {
  return store.pending.find((x) => x.uid === uid);
}

async function handleApi(req, res, urlObj) {
  const p = urlObj.pathname;
  if (p === '/api/health') {
    return json(res, 200, { ok: true, service: 'agent-vault', ts: nowISO() });
  }

  if (p === '/api/uid/request' && req.method === 'POST') {
    const body = await parseBody(req);
    const uid = String(body.uid || '').trim();
    const name = String(body.name || '').trim().slice(0, 60);
    if (!isValidUid(uid)) return json(res, 400, { ok: false, error: 'uid invalid' });

    const store = readStore();
    if (approvedLookup(store, uid)) {
      return json(res, 200, { ok: true, status: 'approved', uid });
    }
    if (!pendingLookup(store, uid)) {
      const reqId = crypto.randomBytes(6).toString('hex');
      const item = { reqId, uid, name, ts: Date.now(), status: 'pending' };
      store.pending.push(item);
      store.audit.push({ at: nowISO(), type: 'request', uid, name });
      writeStore(store);

      const approveUrl = `${PUBLIC_BASE}api/review-link?token=${encodeURIComponent(REVIEW_TOKEN)}&uid=${encodeURIComponent(uid)}&d=approve`;
      const rejectUrl = `${PUBLIC_BASE}api/review-link?token=${encodeURIComponent(REVIEW_TOKEN)}&uid=${encodeURIComponent(uid)}&d=reject`;
      const msg = [
        '【代理小金庫 UID 審核】',
        `UID: ${uid}`,
        `代理: ${name || '(未填)'}`,
        `時間: ${new Date(item.ts).toLocaleString('zh-TW')}`,
        '',
        '請點下方按鈕審核：',
      ].join('\n');
      await tgSend(msg, [
        [{ text: `批准 ${uid}`, url: approveUrl }],
        [{ text: `駁回 ${uid}`, url: rejectUrl }],
      ]);
    }
    return json(res, 200, { ok: true, status: 'pending', uid });
  }

  if (p === '/api/uid/status' && req.method === 'GET') {
    const uid = String(urlObj.searchParams.get('uid') || '').trim();
    if (!isValidUid(uid)) return json(res, 400, { ok: false, error: 'uid invalid' });
    const store = readStore();
    if (approvedLookup(store, uid)) return json(res, 200, { ok: true, status: 'approved', uid });
    if (pendingLookup(store, uid)) return json(res, 200, { ok: true, status: 'pending', uid });
    return json(res, 200, { ok: true, status: 'none', uid });
  }

  if (p === '/api/uid/review' && req.method === 'POST') {
    const body = await parseBody(req);
    if (String(body.token || '') !== REVIEW_TOKEN) return json(res, 403, { ok: false, error: 'forbidden' });
    const uid = String(body.uid || '').trim();
    const decision = String(body.decision || '').trim();
    if (!isValidUid(uid) || !['approve', 'reject'].includes(decision)) return json(res, 400, { ok: false, error: 'invalid params' });
    const store = readStore();
    const idx = store.pending.findIndex((x) => x.uid === uid);
    const rec = idx >= 0 ? store.pending[idx] : { uid, name: '', ts: Date.now() };
    if (idx >= 0) store.pending.splice(idx, 1);
    if (decision === 'approve') {
      if (!approvedLookup(store, uid)) {
        store.approved.push({ uid, name: rec.name || '', ts: rec.ts, approvedTs: Date.now() });
      }
    }
    store.audit.push({ at: nowISO(), type: decision, uid });
    writeStore(store);
    return json(res, 200, { ok: true, decision, uid });
  }

  if (p === '/api/uid/state' && req.method === 'GET') {
    const token = String(urlObj.searchParams.get('token') || '');
    if (token !== REVIEW_TOKEN) return json(res, 403, { ok: false, error: 'forbidden' });
    const store = readStore();
    return json(res, 200, { ok: true, pending: store.pending, approved: store.approved });
  }

  if (p === '/api/review-link' && req.method === 'GET') {
    const token = String(urlObj.searchParams.get('token') || '');
    const uid = String(urlObj.searchParams.get('uid') || '').trim();
    const d = String(urlObj.searchParams.get('d') || '').trim();
    if (token !== REVIEW_TOKEN || !isValidUid(uid) || !['approve', 'reject'].includes(d)) {
      return text(res, 400, 'text/html; charset=utf-8', '<h2>參數錯誤</h2>');
    }
    const store = readStore();
    const idx = store.pending.findIndex((x) => x.uid === uid);
    const rec = idx >= 0 ? store.pending[idx] : { uid, name: '', ts: Date.now() };
    if (idx >= 0) store.pending.splice(idx, 1);
    if (d === 'approve' && !approvedLookup(store, uid)) {
      store.approved.push({ uid, name: rec.name || '', ts: rec.ts, approvedTs: Date.now() });
    }
    store.audit.push({ at: nowISO(), type: d, uid, via: 'tg_link' });
    writeStore(store);
    const msg = d === 'approve' ? '已批准' : '已駁回';
    await tgSend(`✅ UID 審核結果：${uid} ${msg}`);
    return text(
      res,
      200,
      'text/html; charset=utf-8',
      `<!doctype html><html><meta charset="utf-8"/><body style="font-family:sans-serif;background:#101219;color:#f2f4ff;padding:24px"><h2>${msg}</h2><p>UID: ${uid}</p><p>你可以關閉此頁。</p></body></html>`
    );
  }

  return json(res, 404, { ok: false, error: 'not found' });
}

function serveStatic(req, res, urlObj) {
  let pathname = urlObj.pathname;
  if (pathname === '/') pathname = '/index.html';
  const filePath = path.normalize(path.join(ROOT, pathname));
  if (!filePath.startsWith(ROOT) || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
    res.end('Not Found');
    return;
  }
  res.writeHead(200, { 'content-type': MIME[path.extname(filePath)] || 'application/octet-stream' });
  fs.createReadStream(filePath).pipe(res);
}

const server = http.createServer(async (req, res) => {
  try {
    const host = req.headers.host || 'localhost';
    const urlObj = new URL(req.url || '/', `http://${host}`);
    if (urlObj.pathname.startsWith('/api/')) {
      return handleApi(req, res, urlObj);
    }
    return serveStatic(req, res, urlObj);
  } catch (e) {
    json(res, 500, { ok: false, error: 'internal' });
  }
});

server.listen(PORT, HOST, () => {
  ensureStore();
  console.log('[agent-vault] http://' + HOST + ':' + PORT);
});
