#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · 分账卡片渲染器 (render_card)

把 ``split_bill.py`` 的分账结果渲染成一张**自包含**的 HTML 卡片：
无外链 CSS/JS、无网络依赖，打开即可看、截图即可发群。

- 浅色主题，适配微信/飞书/钉钉里截图的观感
- 720px 定宽，手机与桌面截图都不糊
- 突出「本单共省 X 元」与每人应付金额

用法
----
::

    python split_bill.py -i bill.json > split.json
    python render_card.py -i split.json -o card.html --store "麦当劳(中关村店)"
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from typing import Sequence

CSS = """
:root {
  --red: #DA291C;
  --yellow: #FFC72C;
  --ink: #1F1F1F;
  --ink-2: #5A5A5A;
  --ink-3: #8C8C8C;
  --line: #EDE6DC;
  --bg: #FAF7F2;
  --card: #FFFFFF;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 32px 16px;
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.card {
  width: 720px;
  max-width: 100%;
  margin: 0 auto;
  background: var(--card);
  border-radius: 20px;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0,0,0,.08);
}
.head {
  padding: 24px 28px;
  background: linear-gradient(120deg, var(--red) 0%, #B41E12 100%);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.brand { display: flex; align-items: center; gap: 12px; }
.brand .logo {
  width: 40px; height: 40px; border-radius: 12px;
  background: var(--yellow); color: var(--red);
  display: flex; align-items: center; justify-content: center;
  font-size: 22px; font-weight: 800;
}
.brand h1 { margin: 0; font-size: 20px; letter-spacing: .5px; }
.brand p { margin: 4px 0 0; font-size: 12px; opacity: .82; }
.head .meta { text-align: right; font-size: 12px; line-height: 1.7; opacity: .92; }

.hero {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  border-bottom: 1px solid var(--line);
}
.hero div { padding: 18px 20px; text-align: center; }
.hero div + div { border-left: 1px solid var(--line); }
.hero .k { font-size: 12px; color: var(--ink-3); margin-bottom: 6px; }
.hero .v { font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums; }
.hero .v.save { color: var(--red); }
.hero .v.pay { color: var(--red); }
.hero .v.strike { color: var(--ink-3); text-decoration: line-through; font-size: 20px; }

.policy {
  padding: 10px 28px;
  background: #FFFBF0;
  border-bottom: 1px solid var(--line);
  font-size: 12.5px;
  color: var(--ink-2);
  display: flex;
  gap: 10px;
  align-items: center;
}
.tag {
  display: inline-block; padding: 2px 9px; border-radius: 999px;
  background: var(--yellow); color: #6B4A00; font-weight: 600; font-size: 11.5px;
}

.people { padding: 8px 28px 4px; }
.person {
  display: flex; align-items: flex-start; gap: 14px;
  padding: 16px 0; border-bottom: 1px dashed var(--line);
}
.person:last-child { border-bottom: none; }
.avatar {
  flex: 0 0 36px; width: 36px; height: 36px; border-radius: 50%;
  background: #FDECEA; color: var(--red);
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 15px;
}
.person .body { flex: 1; min-width: 0; }
.person .name { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
.person .items { font-size: 12.5px; color: var(--ink-3); line-height: 1.7; word-break: break-all; }
.person .amount { text-align: right; flex: 0 0 132px; }
.person .amount .pay { font-size: 19px; font-weight: 700; font-variant-numeric: tabular-nums; }
.person .amount .sub { font-size: 11.5px; color: var(--ink-3); margin-top: 3px; line-height: 1.6; }

.transfer { padding: 4px 28px 24px; }
.transfer h2 {
  font-size: 13px; color: var(--ink-3); font-weight: 600;
  margin: 8px 0 10px; letter-spacing: .5px;
}
.tr {
  display: flex; align-items: center; justify-content: space-between;
  padding: 11px 14px; border-radius: 10px; background: #FBF8F3;
  font-size: 13.5px; margin-bottom: 8px;
}
.tr .arrow { color: var(--red); font-weight: 700; margin: 0 6px; }
.tr .amt { font-weight: 700; font-variant-numeric: tabular-nums; }

.foot {
  padding: 16px 28px 22px; font-size: 11.5px; color: var(--ink-3);
  line-height: 1.8; border-top: 1px solid var(--line);
}
.foot b { color: var(--ink-2); font-weight: 600; }
.warn {
  margin: 0 28px 20px; padding: 10px 14px; border-radius: 10px;
  background: #FFF6E5; color: #8A6100; font-size: 12.5px;
}
"""


def _esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _initial(name: str) -> str:
    name = str(name).strip()
    if not name:
        return "?"
    return name[0]


def render(data: dict, store: str = "", order_no: str = "", generated_at: str = "") -> str:
    summary = data.get("summary", {})
    shares = data.get("shares", [])
    transfers = data.get("transfers", [])
    policy = data.get("policy", {})

    policy_cn = {
        "proportional": "按商品原价比例分摊",
        "equal": "按人头均摊",
        "owner": "谁用券谁受益",
    }
    d_policy = policy_cn.get(policy.get("discount", ""), policy.get("discount", "-"))
    f_policy = "均摊" if policy.get("fee") == "equal" else "按比例"

    parts: list[str] = []
    parts.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append("<title>麦麦拼单官 · 分账单</title>")
    parts.append(f"<style>{CSS}</style></head><body>")
    parts.append('<div class="card">')

    # ---- 头部 ---- #
    parts.append('<div class="head">')
    parts.append('<div class="brand"><div class="logo">M</div><div>')
    parts.append("<h1>麦麦拼单官 · 分账单</h1>")
    parts.append(f'<p>{_esc(store) if store else "麦当劳拼单"}</p>')
    parts.append("</div></div>")
    meta_bits = []
    if order_no:
        meta_bits.append(f"订单号 {_esc(order_no)}")
    if generated_at:
        meta_bits.append(_esc(generated_at))
    meta_bits.append(f"{len(shares)} 人")
    parts.append('<div class="meta">' + "<br>".join(meta_bits) + "</div>")
    parts.append("</div>")

    # ---- 汇总 ---- #
    parts.append('<div class="hero">')
    parts.append(f'<div><div class="k">商品原价</div><div class="v strike">¥{_esc(summary.get("subtotal", "0.00"))}</div></div>')
    parts.append(f'<div><div class="k">本单共省</div><div class="v save">-¥{_esc(summary.get("discount", "0.00"))}</div></div>')
    parts.append(f'<div><div class="k">实付总额</div><div class="v pay">¥{_esc(summary.get("payable", "0.00"))}</div></div>')
    parts.append("</div>")

    # ---- 口径 ---- #
    parts.append('<div class="policy">')
    parts.append(f'<span class="tag">优惠 {_esc(d_policy)}</span>')
    parts.append(f'<span class="tag">配送/打包费 {_esc(f_policy)}</span>')
    parts.append("<span>分账金额精确到分，合计恒等于实付总额。</span>")
    parts.append("</div>")

    # ---- 每人 ---- #
    parts.append('<div class="people">')
    for s in shares:
        name = _esc(s.get("person", ""))
        items = s.get("items") or []
        items_txt = "、".join(_esc(x) for x in items) if items else "—"
        parts.append('<div class="person">')
        parts.append(f'<div class="avatar">{_esc(_initial(s.get("person", "?")))}</div>')
        parts.append('<div class="body">')
        parts.append(f'<div class="name">{name}</div>')
        parts.append(f'<div class="items">{items_txt}</div>')
        parts.append("</div>")
        parts.append('<div class="amount">')
        parts.append(f'<div class="pay">¥{_esc(s.get("payable", "0.00"))}</div>')
        parts.append(
            f'<div class="sub">原价 ¥{_esc(s.get("subtotal", "0.00"))} '
            f'· 省 ¥{_esc(s.get("discount", "0.00"))}'
            + (f'<br>分摊费用 ¥{_esc(s.get("fee", "0.00"))}' if s.get("fee") not in ("0.00", None) else "")
            + "</div>"
        )
        parts.append("</div></div>")
    parts.append("</div>")

    # ---- 转账 ---- #
    if transfers:
        parts.append('<div class="transfer"><h2>转账（发给垫付人）</h2>')
        for t in transfers:
            parts.append(
                '<div class="tr">'
                f'<span><b>{_esc(t.get("from"))}</b><span class="arrow">→</span>{_esc(t.get("to"))}</span>'
                f'<span class="amt">¥{_esc(t.get("amount"))}</span>'
                "</div>"
            )
        parts.append("</div>")

    note = data.get("note")
    if note:
        parts.append(f'<div class="warn">{_esc(note)}</div>')

    # ---- 页脚 ---- #
    parts.append('<div class="foot">')
    parts.append("<b>麦麦拼单官</b> · 麦当劳程序员创意开发大赛参赛作品，由参赛者独立开发，非麦当劳官方产品。<br>")
    parts.append("餐品信息、价格及优惠以麦当劳官方渠道实时结果为准。分账结果为按所选口径的计算参考，请以实际支付金额为准。")
    parts.append("</div>")

    parts.append("</div></body></html>")
    return "".join(parts)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="render_card",
        description="麦麦拼单官 · 分账卡片渲染器（输出自包含 HTML）",
    )
    p.add_argument("--input", "-i", help="split_bill.py 输出的 JSON；缺省从 stdin 读取")
    p.add_argument("--out", "-o", default="split-card.html", help="输出 HTML 路径")
    p.add_argument("--store", default="", help="门店名称")
    p.add_argument("--order-no", default="", help="订单号")
    p.add_argument("--time", default="", help="生成时间文案")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = json.load(sys.stdin)

    doc = render(data, store=args.store, order_no=args.order_no, generated_at=args.time)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"已生成分账卡片：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
