/**
 * 在线试算器 · 可视检查 (visual smoke test)
 *
 * 作用：用本机已装的 Chrome 打开 docs/index.html，做三件事——
 *   1. 捕获所有 console error / pageerror，有错就非零退出；
 *   2. 断言页面上真的算出了结果（不是渲染了空壳）；
 *   3. 输出 README 用的预览图 assets/preview.png。
 *
 * 为什么不用无头浏览器包：playwright-core 不下载浏览器，直接用
 * channel: 'chrome' 复用本机 Chrome，零额外下载。
 *
 * 用法：
 *   node tests/shot.js                    # 自动起本地服务
 *   SHOT_URL=http://127.0.0.1:8777/ node tests/shot.js
 */

'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');

const ROOT = path.join(__dirname, '..');
const DOCS = path.join(ROOT, 'docs');

const PW = 'C:/Users/myages2025/.workbuddy-ai/binaries/node/workspace/node_modules/playwright-core';
let chromium;
try {
  ({ chromium } = require(PW));
} catch (e) {
  console.error('✗ 未找到 playwright-core，跳过可视检查：' + e.message);
  process.exit(0);
}

/* ---------- 极简静态服务（避免依赖 python） ---------- */
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.gif': 'image/gif'
};

function serve(dir) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let p = decodeURIComponent(req.url.split('?')[0]);
      if (p === '/') p = '/index.html';
      const file = path.join(dir, p);
      if (!file.startsWith(dir) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
        res.writeHead(404); res.end('not found'); return;
      }
      res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' });
      fs.createReadStream(file).pipe(res);
    });
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

(async () => {
  let server = null;
  let url = process.env.SHOT_URL;
  if (!url) {
    const s = await serve(DOCS);
    server = s.server;
    url = `http://127.0.0.1:${s.port}/`;
  }

  const browser = await chromium.launch({
    channel: 'chrome',
    args: ['--no-sandbox', '--disable-dev-shm-usage']
  });

  const errors = [];
  const page = await browser.newPage({
    viewport: { width: 1360, height: 1000 },
    deviceScaleFactor: 2
  });
  page.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text()); });
  page.on('pageerror', (e) => errors.push('[pageerror] ' + e.message));

  await page.goto(url, { waitUntil: 'load' });
  await page.click('#loadSample');
  await page.waitForTimeout(350);

  const probe = await page.evaluate(() => {
    const txt = (s) => { const e = document.querySelector(s); return e ? e.textContent.trim() : null; };
    return {
      title: document.title,
      tiles: [...document.querySelectorAll('#tiles .tile')].map((t) => ({
        k: t.querySelector('.k').textContent.trim(),
        v: t.querySelector('.v').textContent.trim()
      })),
      verify: txt('#verify'),
      sharesRows: document.querySelectorAll('#sharesTable tbody tr').length,
      transferCount: document.querySelectorAll('#transfers .tx').length,
      hasApi: typeof window.MCDSplit === 'object'
    };
  });

  /* ---------- 断言：必须真的算出来了 ---------- */
  const fails = [];
  if (probe.tiles.length !== 4) fails.push('汇总卡片不是 4 个');
  if (probe.sharesRows !== 6) fails.push('分账明细行数应为 6（5 人 + 合计），实际 ' + probe.sharesRows);
  if (probe.transferCount !== 4) fails.push('转账笔数应为 4，实际 ' + probe.transferCount);
  if (!probe.verify || probe.verify.indexOf('恒等校验通过') < 0) fails.push('恒等校验未通过');
  if (!probe.hasApi) fails.push('window.MCDSplit 未挂载');

  /* ---------- 预览图 ---------- */
  fs.mkdirSync(path.join(ROOT, 'assets'), { recursive: true });
  await page.screenshot({
    path: path.join(ROOT, 'assets', 'preview.png'),
    clip: { x: 0, y: 0, width: 1360, height: 1180 }
  });

  await browser.close();
  if (server) server.close();

  console.log(JSON.stringify({ url, probe, errors, fails }, null, 2));
  if (fails.length || errors.length) {
    console.error('✗ 可视检查未通过');
    process.exit(1);
  }
  console.log('✓ 可视检查通过（无 console 报错，页面数据自洽）');
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
