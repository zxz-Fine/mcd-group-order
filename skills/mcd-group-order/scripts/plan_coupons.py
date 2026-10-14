#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · 券组合候选生成器 (plan_coupons)

⚠️ 本模块的模型基于**实测**的麦当劳 MCP 返回结构，与常见假设不同：

麦当劳的优惠券是**按商品绑定**的，不是「满 X 减 Y」的门槛券。``query-store-coupons``
返回的每条券长这样（实测）::

    {
      "title": "中薯条买一送一",
      "couponId": "...",
      "couponCode": "...",
      "tradeDateTime": "...",
      "products": [{"productName": "中薯条", "productCode": "4810"}]
    }

**券数据里没有面额、也没有门槛**——真实优惠额只有 ``calculate-price`` 才知道
（它在 ``data.enjoyed.realDiscount`` / ``data.discount`` 里给出，单位是分）。

因此本脚本的职责被限定为两件事：

1. **建立映射**：把每张券关联到购物车里它能作用的商品（按 ``productCode`` 匹配）。
2. **剪枝出候选**：产出至多 N 个值得一试的「券 × 商品」组合，交由 ``calculate-price``
   权威试算，最终取 ``data.price`` 最低者。

**本脚本不预测优惠金额**，避免给出误导性的「预计省 X 元」。

本脚本**不联网、不消耗 MCP 配额**。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Sequence

from split_bill import cents_to_yuan, yuan_to_cents

MAX_COMBOS = 60

# 容错键名（不同接口/版本可能不同）
_ID_KEYS = ("couponId", "coupon_id", "id", "activityId")
_CODE_KEYS = ("couponCode", "coupon_code", "code")
_TITLE_KEYS = ("title", "couponName", "name", "couponTitle", "activityName")
_DATE_KEYS = ("tradeDateTime", "validDate", "expireTime", "validEndTime")
_PRODUCT_LIST_KEYS = ("products", "productList", "applicableProducts", "items")
# 面额/门槛为**可选**增强字段：真实券数据通常没有
_AMOUNT_KEYS = ("discountAmount", "amount", "value", "faceValue", "reduceAmount")
_THRESHOLD_KEYS = ("threshold", "minAmount", "useCondition", "limitAmount")


@dataclass
class Coupon:
    cid: str
    name: str
    codes: list[str] = field(default_factory=list)
    products: list[dict] = field(default_factory=list)  # [{productCode, productName}]
    trade_datetime: str = ""
    amount_cents: int = 0     # 可选
    threshold_cents: int = 0  # 可选
    raw: dict = field(default_factory=dict)

    @property
    def product_codes(self) -> set[str]:
        return {str(p.get("productCode") or p.get("code") or "") for p in self.products} - {""}

    @property
    def product_names(self) -> list[str]:
        return [
            str(p.get("productName") or p.get("name") or "")
            for p in self.products
            if (p.get("productName") or p.get("name"))
        ]

    def to_dict(self) -> dict:
        return {
            "coupon_id": self.cid,
            "name": self.name,
            "coupon_codes": self.codes,
            "applies_to": self.product_names or sorted(self.product_codes),
            "trade_datetime": self.trade_datetime,
        }


def _first(raw: dict, keys: Sequence[str]):
    for k in keys:
        v = raw.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def normalize_coupons(raw_list) -> tuple[list[Coupon], list[dict]]:
    """把各种形态的券输入归一为 ``Coupon`` 列表。

    兼容：MCP 原始响应（``{"data": [...]}``）、纯数组、单对象。
    """
    if isinstance(raw_list, dict):
        node = raw_list
        for key in ("data", "result"):
            # 必须判断 node 仍是 dict：一旦下钻到数组就要停止，
            # 否则下一轮 node.get() 会抛 AttributeError（实测踩过）。
            if not isinstance(node, dict):
                break
            nxt = node.get(key)
            if isinstance(nxt, (list, dict)):
                node = nxt
        raw_list = node if isinstance(node, list) else [node]
    if not isinstance(raw_list, list):
        return [], [{"reason": "券输入不是数组", "raw": raw_list}]

    coupons: list[Coupon] = []
    skipped: list[dict] = []

    for i, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            skipped.append({"index": i, "reason": "非对象结构"})
            continue

        title = _first(raw, _TITLE_KEYS)
        if not title:
            skipped.append({"index": i, "reason": "缺少券名，已跳过"})
            continue

        raw_code = _first(raw, _CODE_KEYS)
        if isinstance(raw_code, list):
            codes = [str(c) for c in raw_code if c]
        else:
            codes = [str(raw_code)] if raw_code else []

        products = []
        pl = _first(raw, _PRODUCT_LIST_KEYS)
        if isinstance(pl, list):
            products = [p for p in pl if isinstance(p, dict)]
        elif isinstance(pl, dict):
            products = [pl]

        amount = _first(raw, _AMOUNT_KEYS)
        threshold = _first(raw, _THRESHOLD_KEYS)

        coupons.append(
            Coupon(
                cid=str(_first(raw, _ID_KEYS) or f"coupon-{i}"),
                name=str(title).strip(),
                codes=codes,
                products=products,
                trade_datetime=str(_first(raw, _DATE_KEYS) or ""),
                amount_cents=yuan_to_cents(amount) if amount is not None else 0,
                threshold_cents=yuan_to_cents(threshold) if threshold is not None else 0,
                raw=raw,
            )
        )

    return coupons, skipped


def plan(
    raw_coupons,
    cart_items: Sequence[dict],
    top_k: int = 8,
    max_stack: int = 2,
) -> dict:
    """生成券组合候选。

    :param cart_items: ``[{productCode, name, unit_price_cents, qty, person}]``
    :param max_stack: 单个候选里最多同时使用几张券（真实场景多为 1 张）
    """
    coupons, skipped = normalize_coupons(raw_coupons)
    warnings: list[str] = []

    # productCode -> 该编码在购物车中的原始小计（分）
    cart_value: dict[str, int] = {}
    for it in cart_items:
        code = str(it.get("productCode") or it.get("code") or "")
        if not code:
            continue
        cart_value[code] = cart_value.get(code, 0) + int(it.get("unit_price_cents", 0)) * int(
            it.get("qty", 1) or 1
        )

    if not cart_value:
        return {
            "candidates": [{"label": "不使用优惠券（基线）", "apply": [], "covered_value": "0.00"}],
            "usable": [],
            "unusable": [],
            "warnings": ["购物车为空或缺少 productCode，无法生成券候选。"],
            "next_step": "请先用 match_menu.py 得到带 productCode 的购物车。",
        }

    usable: list[dict] = []
    unusable: list[dict] = []

    for c in coupons:
        covered = [code for code in c.product_codes if code in cart_value]
        if not covered:
            unusable.append(
                {
                    **c.to_dict(),
                    "reason": "本单没有该券适用的商品",
                    "missing_products": c.product_names,
                }
            )
            continue
        usable.append(
            {
                "coupon": c,
                "covered_codes": covered,
                "covered_value_cents": sum(cart_value[x] for x in covered),
            }
        )

    # 覆盖金额降序：覆盖越贵的商品越可能省得多（弱启发式，仅用于排序与剪枝）
    usable.sort(key=lambda x: -x["covered_value_cents"])

    # ---- 候选生成 ---- #
    candidates: list[dict] = [{"label": "不使用优惠券（基线）", "apply": [], "_v": 0, "_n": 0}]

    # 1) 单券候选：全部保留，一张都不漏
    for u in usable:
        c: Coupon = u["coupon"]
        candidates.append(
            {
                "label": c.name,
                "apply": [
                    {
                        "productCode": code,
                        "couponId": c.cid,
                        "couponCode": c.codes[0] if c.codes else "",
                    }
                    for code in u["covered_codes"]
                ],
                "_v": u["covered_value_cents"],
                "_n": 1,
            }
        )

    # 2) 贪心叠加：券互不覆盖商品时才叠加
    if max_stack >= 2 and len(usable) >= 2:
        used: set[str] = set()
        combo: list[dict] = []
        labels: list[str] = []
        value = 0
        for u in usable:
            c: Coupon = u["coupon"]
            if used & set(u["covered_codes"]):
                continue
            used |= set(u["covered_codes"])
            value += u["covered_value_cents"]
            labels.append(c.name)
            combo.append(
                {
                    "productCode": u["covered_codes"][0],
                    "couponId": c.cid,
                    "couponCode": c.codes[0] if c.codes else "",
                }
            )
            if len(combo) >= max_stack:
                break
        if len(combo) >= 2:
            candidates.append(
                {"label": " + ".join(labels), "apply": combo, "_v": value, "_n": len(combo)}
            )

    if len(candidates) > MAX_COMBOS:
        warnings.append(f"候选数超过上限 {MAX_COMBOS}，已截断。")
        candidates = candidates[:MAX_COMBOS]

    # 排序：覆盖金额降序，其次用券数升序（少用券更简单）
    candidates.sort(key=lambda x: (-x["_v"], x["_n"]))

    seen: set[tuple] = set()
    out: list[dict] = []
    for c in candidates:
        key = tuple(sorted((a["productCode"], a["couponId"]) for a in c["apply"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "label": c["label"],
                "apply": c["apply"],
                "covered_value": cents_to_yuan(c["_v"]),
            }
        )
        if len(out) >= top_k:
            break

    return {
        "cart_value": cents_to_yuan(sum(cart_value.values())),
        "coupon_total": len(coupons),
        "usable": [u["coupon"].to_dict() for u in usable],
        "unusable": unusable,
        "candidates": out,
        "skipped": skipped,
        "warnings": warnings,
        "next_step": (
            "对 candidates 中的每个组合，把 apply 里的 couponId/couponCode 挂到对应 "
            "productCode 上调用 MCP 工具 calculate-price 试算，取 data.price（单位：分）"
            "最低者为最终方案。注意：本脚本不预测优惠金额，一切以 calculate-price 为准。"
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="plan_coupons",
        description="麦麦拼单官 · 券组合候选生成器（按商品绑定模型，本地运行不消耗配额）",
    )
    p.add_argument("--input", "-i", help="输入 JSON（含 coupons 与 cart_items）；缺省从 stdin 读取")
    p.add_argument("--top-k", type=int, default=8, help="返回的候选组合数上限（默认 8）")
    p.add_argument("--max-stack", type=int, default=2, help="单个候选最多叠加几张券（默认 2）")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        payload = json.load(sys.stdin)

    cart = payload.get("cart_items") or payload.get("items") or []
    result = plan(
        raw_coupons=payload.get("coupons", []),
        cart_items=cart,
        top_k=args.top_k,
        max_stack=args.max_stack,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
