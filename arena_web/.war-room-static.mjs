import http from 'http';
import fs from 'fs';
import path from 'path';
import crypto from 'crypto';

const root = process.cwd();
const port = Number(process.env.PORT || 3000);
const dataDir = path.join(root, '.data');
const storePath = path.join(dataDir, 'uid_store.json');
const reviewToken = process.env.AGENT_REVIEW_TOKEN || '@Jacky87084';
const tgToken = process.env.ARENA_TG_TOKEN || '8941144271:AAEx0n2xY2gDiDewg1VglE1qvFRuwj7hBck';
const tgChats = (process.env.ARENA_TG_CHATS || '6312951992').split(',').map((s) => s.trim()).filter(Boolean);
const publicBase = (process.env.AGENT_VAULT_PUBLIC_BASE || 'https://108.160.139.47/war-room/apps/p-6e6dee8f/').replace(/\/?$/, '/');

const types = {
  '.html': 'text/html;charset=utf-8',
  '.htm': 'text/html;charset=utf-8',
  '.js': 'text/javascript',
  '.mjs': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.webp': 'image/webp',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.txt': 'text/plain;charset=utf-8',
};

function ensureStore() {
  if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });
  if (!fs.existsSync(storePath)) {
    fs.writeFileSync(storePath, JSON.stringify({ pending: [], approved: [], audit: [] }, null, 2), 'utf8');
  }
}

function loadStore() {
  ensureStore();
  try {
    const d = JSON.parse(fs.readFileSync(storePath, 'utf8'));
    d.pending = Array.isArray(d.pending) ? d.pending : [];
    d.approved = Array.isArray(d.approved) ? d.approved : [];
    d.audit = Array.isArray(d.audit) ? d.audit : [];
    return d;
  } catch {
    return { pending: [], approved: [], audit: [] };
  }
}

function saveStore(d) {
  ensureStore();
  fs.writeFileSync(storePath, JSON.stringify(d, null, 2), 'utf8');
}

function isUid(uid) {
  return /^[0-9]{5,20}$/.test(uid || '');
}

function sendJson(res, code, payload) {
  res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
  res.end(JSON.stringify(payload));
}

function readBody(req) {
  return new Promise((resolve) => {
    let body = '';
    req.on('data', (c) => {
      body += c.toString('utf8');
      if (body.length > 1_000_000) req.destroy();
    });
    req.on('end', () => {
      if (!body) return resolve({});
      try { resolve(JSON.parse(body)); } catch { resolve({}); }
    });
    req.on('error', () => resolve({}));
  });
}

async function tgSend(text, inline) {
  if (!tgToken || !tgChats.length) return;
  const url = `https://api.telegram.org/bot${tgToken}/sendMessage`;
  for (const chatId of tgChats) {
    const body = {
      chat_id: chatId,
      text,
      disable_web_page_preview: true,
    };
    if (inline) body.reply_markup = { inline_keyboard: inline };
    try {
      await fetch(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch {}
  }
}

async function handleApi(req, res, reqUrl) {
  if (reqUrl.pathname === '/api/health') {
    return sendJson(res, 200, { ok: true, service: 'agent-vault', ts: new Date().toISOString() });
  }

  if (reqUrl.pathname === '/api/uid/request' && req.method === 'POST') {
    const body = await readBody(req);
    const uid = String(body.uid || '').trim();
    const name = String(body.name || '').trim().slice(0, 60);
    if (!isUid(uid)) return sendJson(res, 400, { ok: false, error: 'uid invalid' });

    const store = loadStore();
    const approved = store.approved.find((x) => x.uid === uid);
    if (approved) return sendJson(res, 200, { ok: true, status: 'approved', uid });

    const pending = store.pending.find((x) => x.uid === uid);
    if (!pending) {
      const item = { reqId: crypto.randomBytes(6).toString('hex'), uid, name, ts: Date.now(), status: 'pending' };
      store.pending.push(item);
      store.audit.push({ at: new Date().toISOString(), type: 'request', uid, name });
      saveStore(store);

      const approveUrl = `${publicBase}api/review-link?token=${encodeURIComponent(reviewToken)}&uid=${encodeURIComponent(uid)}&d=approve`;
      const rejectUrl = `${publicBase}api/review-link?token=${encodeURIComponent(reviewToken)}&uid=${encodeURIComponent(uid)}&d=reject`;
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
    return sendJson(res, 200, { ok: true, status: 'pending', uid });
  }

  if (reqUrl.pathname === '/api/uid/status' && req.method === 'GET') {
    const uid = String(reqUrl.searchParams.get('uid') || '').trim();
    if (!isUid(uid)) return sendJson(res, 400, { ok: false, error: 'uid invalid' });
    const store = loadStore();
    if (store.approved.find((x) => x.uid === uid)) return sendJson(res, 200, { ok: true, status: 'approved', uid });
    if (store.pending.find((x) => x.uid === uid)) return sendJson(res, 200, { ok: true, status: 'pending', uid });
    return sendJson(res, 200, { ok: true, status: 'none', uid });
  }

  if (reqUrl.pathname === '/api/uid/state' && req.method === 'GET') {
    const token = String(reqUrl.searchParams.get('token') || '');
    if (token !== reviewToken) return sendJson(res, 403, { ok: false, error: 'forbidden' });
    const store = loadStore();
    return sendJson(res, 200, { ok: true, pending: store.pending, approved: store.approved });
  }

  if (reqUrl.pathname === '/api/uid/review' && req.method === 'POST') {
    const body = await readBody(req);
    if (String(body.token || '') !== reviewToken) return sendJson(res, 403, { ok: false, error: 'forbidden' });
    const uid = String(body.uid || '').trim();
    const decision = String(body.decision || '').trim();
    if (!isUid(uid) || !['approve', 'reject'].includes(decision)) return sendJson(res, 400, { ok: false, error: 'invalid params' });

    const store = loadStore();
    const idx = store.pending.findIndex((x) => x.uid === uid);
    const rec = idx >= 0 ? store.pending[idx] : { uid, name: '', ts: Date.now() };
    if (idx >= 0) store.pending.splice(idx, 1);
    if (decision === 'approve' && !store.approved.find((x) => x.uid === uid)) {
      store.approved.push({ uid, name: rec.name || '', ts: rec.ts, approvedTs: Date.now() });
    }
    store.audit.push({ at: new Date().toISOString(), type: decision, uid });
    saveStore(store);
    return sendJson(res, 200, { ok: true, decision, uid });
  }

  if (reqUrl.pathname === '/api/review-link' && req.method === 'GET') {
    const token = String(reqUrl.searchParams.get('token') || '');
    const uid = String(reqUrl.searchParams.get('uid') || '').trim();
    const d = String(reqUrl.searchParams.get('d') || '').trim();
    if (token !== reviewToken || !isUid(uid) || !['approve', 'reject'].includes(d)) {
      res.writeHead(400, { 'content-type': 'text/html; charset=utf-8' });
      res.end('<h2>參數錯誤</h2>');
      return;
    }
    const store = loadStore();
    const idx = store.pending.findIndex((x) => x.uid === uid);
    const rec = idx >= 0 ? store.pending[idx] : { uid, name: '', ts: Date.now() };
    if (idx >= 0) store.pending.splice(idx, 1);
    if (d === 'approve' && !store.approved.find((x) => x.uid === uid)) {
      store.approved.push({ uid, name: rec.name || '', ts: rec.ts, approvedTs: Date.now() });
    }
    store.audit.push({ at: new Date().toISOString(), type: d, uid, via: 'tg_link' });
    saveStore(store);

    const msg = d === 'approve' ? '已批准' : '已駁回';
    await tgSend(`✅ UID 審核結果：${uid} ${msg}`);
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    res.end(`<!doctype html><html><meta charset="utf-8"/><body style="font-family:sans-serif;background:#101219;color:#f2f4ff;padding:24px"><h2>${msg}</h2><p>UID: ${uid}</p><p>你可以關閉此頁。</p></body></html>`);
    return;
  }

  return sendJson(res, 404, { ok: false, error: 'not found' });
}

function serveStatic(req, res) {
  let p = decodeURIComponent((req.url || '/').split('?')[0]);
  if (p.endsWith('/')) p += 'index.html';
  const file = path.join(root, p);
  if (!file.startsWith(root)) {
    res.writeHead(403);
    res.end('Forbidden');
    return;
  }
  fs.readFile(file, (e, d) => {
    if (e) {
      fs.readFile(path.join(root, 'index.html'), (e2, d2) => {
        if (e2) {
          res.writeHead(404);
          res.end('Not found');
        } else {
          res.writeHead(200, { 'content-type': 'text/html;charset=utf-8' });
          res.end(d2);
        }
      });
      return;
    }
    res.writeHead(200, { 'content-type': types[path.extname(file).toLowerCase()] || 'application/octet-stream' });
    res.end(d);
  });
}

http.createServer(async (req, res) => {
  const host = req.headers.host || 'localhost';
  const reqUrl = new URL(req.url || '/', `http://${host}`);
  if (reqUrl.pathname.startsWith('/api/')) {
    return handleApi(req, res, reqUrl);
  }
  return serveStatic(req, res);
}).listen(port, '127.0.0.1', () => console.log('static server on ' + port));
