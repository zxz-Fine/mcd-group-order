/**
 * 前后端算法一致性校验（parity check）
 *
 * 目的：证明 docs/index.html 里的浏览器 JS 分账引擎
 *      与 skills/mcd-group-order/scripts/split_bill.py 逐位一致。
 *
 * 做法：不复制代码，而是把**真正要发布的那份 HTML** 里的 <script>
 *      在 Node 里执行（DOM 用桩件替代），再拿结果和 Python 输出逐字节对比。
 *      这样校验的是「实际产物」，而不是「我以为的产物」。
 *
 * 用法：
 *   node tests/parity_check.js <bill.json> [markdown]
 */
'use strict';

const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', 'docs', 'index.html');

/* ---------- 1. 从 HTML 里抽出唯一的 <script> ---------- */
const html = fs.readFileSync(HTML, 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('✗ 在 docs/index.html 里找不到 <script> 块'); process.exit(2); }
const source = m[1];

/* ---------- 2. 给 DOM 打桩，让脚本能在 Node 里跑完 ---------- */
function fakeEl() {
  return {
    innerHTML: '', value: '', textContent: '', style: {}, dataset: {},
    addEventListener() {}, appendChild() {}, removeChild() {},
    querySelectorAll() { return { forEach() {} }; },
    closest() { return null; },
    select() {}
  };
}
global.document = {
  getElementById: () => fakeEl(),
  createElement: () => fakeEl(),
  body: { appendChild() {}, removeChild() {} }
};
// Node 18+ 自带只读的 globalThis.navigator，直接赋值会抛 TypeError
Object.defineProperty(global, 'navigator', {
  value: {}, writable: true, configurable: true
});
if (!global.window) global.window = {};

/* ---------- 3. 执行真实产物，取回引擎 ---------- */
new Function(source)();
const M = global.window.MCDSplit;
if (!M) { console.error('✗ 未能从页面中取到 window.MCDSplit'); process.exit(2); }

/* ---------- 4. 读账单，转成引擎输入 ---------- */
function loadBill(file) {
  const p = JSON.parse(fs.readFileSync(file, 'utf8'));
  const toCents = (v) => Math.round(parseFloat(v || 0) * 100);
  const pick = (ck, yk) => (p[ck] !== undefined ? p[ck] : toCents(p[yk]));
  return {
    items: (p.items || []).map((d) => ({
      person: String(d.person).trim(),
      name: String(d.name || '').trim(),
      unitPriceCents: d.unit_price_cents !== undefined
        ? d.unit_price_cents
        : toCents(d.unit_price !== undefined ? d.unit_price : (d.price || 0)),
      qty: d.qty || d.quantity || 1
    })),
    opts: {
      discountCents: pick('discount_cents', 'discount'),
      deliveryFeeCents: pick('delivery_fee_cents', 'delivery_fee'),
      packagingFeeCents: pick('packaging_fee_cents', 'packaging_fee'),
      discountPolicy: p.discount_policy || 'proportional',
      feePolicy: p.fee_policy || 'equal',
      payer: p.payer,
      discountOwner: p.discount_owner,
      note: p.note || ''
    }
  };
}

/* ---------- 5a. 批量模式：从 stdin 读多组用例，一次跑完 ---------- */
if (process.argv[2] === '--batch') {
  let raw = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (c) => { raw += c; });
  process.stdin.on('end', () => {
    const cases = JSON.parse(raw).cases || [];
    const results = cases.map((c) => {
      const r = M.splitBill(c.items, c.opts);
      if (r.error) return { error: r.error };
      const v = M.verifyResult(r);
      return {
        json: JSON.parse(M.toJson(r)),
        markdown: M.toMarkdown(r),
        verify: {
          sharesOk: v.sharesOk,
          orderOk: v.orderOk,
          sumShares: v.sumShares,
          expected: v.expected,
          overflowCents: r.overflowCents,
          payableCents: r.payableCents,
          subtotalCents: r.subtotalCents,
          discountCents: r.discountCents,
          resolvedPolicy: r.discountPolicy
        }
      };
    });
    process.stdout.write(JSON.stringify({ results }));
  });
} else {
  /* ---------- 5b. 单文件模式 ---------- */
  const bill = loadBill(process.argv[2] || path.join(__dirname, '..', 'examples', 'sample_bill.json'));
  const r = M.splitBill(bill.items, bill.opts);
  const v = M.verifyResult(r);

  if (process.argv[3] === 'markdown') {
    process.stdout.write(M.toMarkdown(r) + '\n');
  } else {
    process.stdout.write(M.toJson(r) + '\n');
  }
  process.stderr.write('恒等校验: sharesOk=' + v.sharesOk + ' orderOk=' + v.orderOk +
    ' | Σ明细=' + M.centsToYuan(v.sumShares) +
    ' 订单实付=' + M.centsToYuan(r.payableCents) +
    ' overflow=' + M.centsToYuan(r.overflowCents) + '\n');
}
