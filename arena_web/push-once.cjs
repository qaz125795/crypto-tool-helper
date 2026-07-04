'use strict';
const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const BASE = (process.env.WR_URL || '').replace(/\/$/, '');
const SLUG = process.env.WR_SLUG;
const TOKEN = process.env.WR_TOKEN;
if (process.env.WR_INSECURE === '1') process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';

if (!BASE || !SLUG || !TOKEN) {
  console.error('缺少 WR_URL / WR_SLUG / WR_TOKEN');
  process.exit(1);
}

const ROOT = process.cwd();
const IGNORE = ['node_modules', '.git', '.next', 'dist', 'out', '.war-room', '.turbo', '.vercel', '_probe'];

function log(msg) {
  console.log('[戰情室] ' + msg);
}

function makeTar() {
  const tmp = path.join(os.tmpdir(), 'wr-' + SLUG + '-' + Date.now() + '.tgz');
  const args = ['-czf', tmp];
  for (const d of IGNORE) args.push('--exclude=./' + d);
  args.push('-C', ROOT, '.');
  const r = spawnSync('tar', args, { stdio: 'ignore' });
  if (r.status !== 0 || !fs.existsSync(tmp)) return null;
  return tmp;
}

(async function () {
  log('資料夾：' + ROOT);
  log('專案代號：' + SLUG);
  const tar = makeTar();
  if (!tar) {
    log('打包失敗：找不到 tar');
    process.exit(1);
  }
  const buf = fs.readFileSync(tar);
  try { fs.unlinkSync(tar); } catch (e) {}
  log('上傳中… ' + (buf.length / 1048576).toFixed(2) + ' MB');
  const res = await fetch(BASE + '/api/deploy/sync/' + SLUG, {
    method: 'POST',
    headers: { 'content-type': 'application/octet-stream', 'x-deploy-token': TOKEN },
    body: buf,
  });
  if (!res.ok) {
    const t = await res.text().catch(() => '');
    log('❌ 上傳失敗 HTTP ' + res.status + ' ' + t.slice(0, 300));
    process.exit(1);
  }
  log('✅ 已上傳，伺服器建置中（約 1～3 分鐘）');
  log('   應用：' + BASE + '/apps/' + SLUG);
})();
