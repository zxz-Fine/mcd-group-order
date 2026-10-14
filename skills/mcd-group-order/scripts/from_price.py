#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · 算价结果转分账输入 (from_price)

把麦当劳 MCP ``calculate-price`` 的**原始响应**，直接转成 ``split_bill.py`` 的输入。

为什么需要这一步
----------------
``calculate-price`` 返回的金额单位是**分**，并且带有权威的优惠与费用明细::

    data.originalPrice   原价（分）
    data.discount        优惠金额（分）
    data.deliveryPrice   运费现价（分）
    data.packingPrice    打包费现价（分）
    data.price           应付现价（分）
    data.productList[]   {productCode, productName, quantity, originalSubtotal, subtotal}

而分账需要知道「**谁**买了什么」。``productList`` 只按商品聚合，不含人。
所以本脚本的输入是「算价响应 + 报单归属」，输出是带**精确小计**的分账输入。

关键设计：**按商品把小计分摊到人，用最大余额法保证精确**
------------------------------------------------------
某个商品编码的原价小计是 ``originalSubtotal``，但可能有多个人点了它
（例如「2 个巨无霸」由张三、李四各要一个）。
此时用 ``split_bill.allocate()`` 按各自数量比例把这个小计分摊到人，
**保证分摊之和精确等于 originalSubtotal**，避免「除法除不尽」导致的尾差。

用法
----
::

    python from_price.py --price price.json --orders orders.json --out bill.json

``orders.json`` 形如::

    [{"person": "张三", "productCode": "1100", "qty": 1}]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from split_bill import allocate, cents_to_yuan


def _unwrap(payload: dict) -> dict:
    """剥掉 MCP 响应的外层，拿到 data。"""
    if not isinstance(payload, dict):
        raise ValueError("算价响应必须是对象")
    node = payload
    for key in ("data", "result"):
        nxt = node.get(key)
        if isinstance(nxt, dict):
            node = nxt
    return node


def convert(price_payload: dict, orders: Sequence[dict], **overrides) -> dict:
    """把算价响应 + 报单归属转成 split_bill 输入。"""
    data = _unwrap(price_payload)

    product_list = data.get("productList") or []
    if not isinstance(product_list, list) or not product_list:
        raise ValueError("算价响应里没有 productList，无法转换")

    # productCode -> {name, originalSubtotal, subtotal, quantity}
    catalog: dict[str, dict] = {}
    for p in product_list:
        if not isinstance(p, dict):
            continue
        code = str(p.get("productCode") or "")
        if not code:
            continue
        catalog[code] = {
            "name": str(p.get("productName") or ""),
            "original_subtotal": int(p.get("originalSubtotal") or 0),
            "subtotal": int(p.get("subtotal") or 0),
            "quantity": int(p.get("quantity") or 1) or 1,
        }

    # 按商品编码聚合报单
    by_code: dict[str, list[dict]] = {}
    for o in orders:
        code = str(o.get("productCode") or "")
        if code not in catalog:
            continue
        by_code.setdefault(code, []).append(
            {"person": str(o.get("person") or "").strip(), "qty": int(o.get("qty", 1) or 1)}
        )

    warnings: list[str] = []
    items: list[dict] = []

    for code, cat in catalog.items():
        claimants = by_code.get(code)
        if not claimants:
            warnings.append(
                f"商品 {cat['name']}({code}) 出现在算价结果里，但报单中没有归属人，"
                "已按「未分配」挂到一个占位人名下，请人工确认。"
            )
            claimants = [{"person": "__未分配__", "qty": cat["quantity"]}]

        # 用最大余额法把该商品的原价小计精确分摊到各人
        weights = [c["qty"] for c in claimants]
        parts = allocate(cat["original_subtotal"], weights)

        for c, part in zip(claimants, parts):
            items.append(
                {
                    "person": c["person"],
                    "name": cat["name"] or code,
                    "productCode": code,
                    "qty": c["qty"],
                    "subtotal_cents": part,
                }
            )

    bill = {
        "items": items,
        # calculate-price 的金额字段已经是「分」，这里直接透传，不做换算
        "discount_cents": int(data.get("discount") or 0),
        "delivery_fee_cents": int(data.get("deliveryPrice") or 0),
        "packaging_fee_cents": int(data.get("packingPrice") or 0),
        "note": (
            f"价格来源：麦当劳 MCP calculate-price。"
            f"原价 ¥{cents_to_yuan(int(data.get('originalPrice') or 0))}，"
            f"优惠 ¥{cents_to_yuan(int(data.get('discount') or 0))}，"
            f"应付 ¥{cents_to_yuan(int(data.get('price') or 0))}。"
        ),
    }
    bill.update({k: v for k, v in overrides.items() if v is not None})

    # 自检：分账输入算出来的应付，必须与算价接口给出的 price 一致
    expected = (
        sum(i["subtotal_cents"] for i in items)
        - bill["discount_cents"]
        + bill["delivery_fee_cents"]
        + bill["packaging_fee_cents"]
    )
    actual = int(data.get("price") or 0)
    if expected != actual:
        warnings.append(
            f"⚠️ 金额不自洽：由明细推出的应付 ¥{cents_to_yuan(expected)} "
            f"≠ 算价接口给出的 ¥{cents_to_yuan(actual)}。"
            "请勿直接采用本结果，需回查报单归属或算价入参。"
        )
    else:
        bill["_verified"] = True

    if warnings:
        bill["_warnings"] = warnings

    return bill


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="from_price",
        description="麦麦拼单官 · 把 MCP calculate-price 响应转为分账输入（金额单位全程为分）",
    )
    p.add_argument("--price", required=True, help="calculate-price 的原始响应 JSON 文件")
    p.add_argument("--orders", required=True, help="报单归属 JSON：[{person, productCode, qty}]")
    p.add_argument("--out", "-o", help="输出文件路径；缺省打印到 stdout")
    p.add_argument("--payer", help="垫付人")
    p.add_argument("--discount-policy", help="分账口径：proportional / equal / owner")
    p.add_argument("--fee-policy", help="费用口径：equal / proportional")
    p.add_argument("--discount-owner", help="owner 口径下独享优惠的人")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    with open(args.price, "r", encoding="utf-8") as fh:
        price_payload = json.load(fh)
    with open(args.orders, "r", encoding="utf-8") as fh:
        orders = json.load(fh)
    if isinstance(orders, dict):
        orders = orders.get("orders") or orders.get("items") or []

    bill = convert(
        price_payload,
        orders,
        payer=args.payer,
        discount_policy=args.discount_policy,
        fee_policy=args.fee_policy,
        discount_owner=args.discount_owner,
    )

    text = json.dumps(bill, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"已生成分账输入：{args.out}")
        for w in bill.get("_warnings", []):
            print(f"  ⚠ {w}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
