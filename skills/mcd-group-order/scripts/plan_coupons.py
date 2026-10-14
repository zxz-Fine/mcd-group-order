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
import re
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

# 券名/券商品名里常见的**营销后缀**。这些词描述的是优惠形式而非商品本体，
# 归一化时必须剥离，否则「中薯条买一送一」永远对不上购物车里的「中薯条」。
_MARKETING_WORDS = (
    "买一送一", "第二份半价", "第二杯半价", "第二份1元", "第二杯1元",
    "半价", "特价", "特惠", "优惠券", "优惠", "折扣", "立减", "限时", "免费", "券",
)
_RE_PRICE_TOKEN = re.compile(r"\d+(?:\.\d+)?\s*元")
_RE_NOISE = re.compile(r"[\s\-—–·、,，。.（）()【】\[\]/|]+")


def _norm_name(s: str) -> str:
    """把商品名归一化为可比较的形态：剥离价格数字、营销词、符号。

    例：``"9.9元中杯冰美式"`` → ``"中杯冰美式"``；``"中薯条买一送一"`` → ``"中薯条"``。
    """
    s = str(s or "")
    s = _RE_PRICE_TOKEN.sub("", s)
    s = s.replace("™", "").replace("®", "").replace("©", "")
    for w in _MARKETING_WORDS:
        s = s.replace(w, "")
    return _RE_NOISE.sub("", s).strip().lower()


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


def match_coupon_products(coupon: Coupon, cart_items: Sequence[dict]) -> list[dict]:
    """把一张券的适用商品映射到购物车里的真实条目。

    麦当劳的**券商品编码**（如 ``9900012387``）与**菜单/购物车商品编码**（如 ``4810``）
    经常对不上——前者是「活动虚拟商品」，后者是「实际餐品」。实测确认：
    「中薯条买一送一」的券编码是 9900012387，而菜单里中薯条是 4810。
    只按 productCode 精确匹配会把这类券全部误判为「本单不可用」。

    因此采用三级匹配，逐级降信：

    ==============  ============================================  ======
    匹配方式          说明                                          置信
    ==============  ============================================  ======
    ``exact_code``    券的 productCode == 购物车 productCode       1.0
    ``name_exact``    归一化后商品名完全相等                         0.9
    ``name_contains`` 归一化后一方包含另一方（券名常带规格后缀）      0.7
    ==============  ============================================  ======

    返回 ``[{productCode, name, couponProductCode, couponProductName, match, confidence}]``：
    ``productCode`` 是**购物车里的真实编码**（下单要用它），
    ``couponProductCode`` 是券里的编码（``calculate-price`` 试算时可能需要它）。
    """
    matches: list[dict] = []
    for it in cart_items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("productCode") or it.get("code") or "")
        name = str(it.get("name") or it.get("productName") or "")
        if not code:
            continue
        best: dict | None = None
        for cp in coupon.products:
            if not isinstance(cp, dict):
                continue
            ccode = str(cp.get("productCode") or cp.get("code") or "")
            cname = str(cp.get("productName") or cp.get("name") or "")
            conf, how = 0.0, ""
            if ccode and ccode == code:
                conf, how = 1.0, "exact_code"
            elif cname and name:
                a, b = _norm_name(cname), _norm_name(name)
                if a and a == b:
                    conf, how = 0.9, "name_exact"
                elif a and b and len(min(a, b, key=len)) >= 2 and (a in b or b in a):
                    conf, how = 0.7, "name_contains"
            if conf > (best or {}).get("confidence", 0.0):
                best = {
                    "productCode": code,
                    "name": name,
                    "couponProductCode": ccode,
                    "couponProductName": cname,
                    "same_code": bool(ccode and ccode == code),
                    "match": how,
                    "confidence": conf,
                }
        if best:
            matches.append(best)
    return matches


def plan(
    raw_coupons,
    cart_items: Sequence[dict],
    top_k: int = 8,
    max_stack: int = 1,
) -> dict:
    """生成券候选。

    :param cart_items: ``[{productCode, name, unit_price_cents, qty, person}]``
    :param max_stack: **强制为 1**。实测 ``calculate-price`` 一次传 2 张券会直接返回
        ``code=600022 "暂不支持多张券使用"``——**麦当劳不支持叠加券**，
        所以不存在「券组合优化」，只有「单券择优」。传 >1 会被降为 1 并告警。
    """
    coupons, skipped = normalize_coupons(raw_coupons)
    warnings: list[str] = []

    # 实测：calculate-price 一次只接受 1 张券，叠加会直接报错
    if max_stack > 1:
        warnings.append(
            f"max_stack={max_stack} 已强制降为 1：实测 calculate-price 一次只支持 1 张券"
            "（传 2 张返回 code=600022「暂不支持多张券使用」），麦当劳不存在券叠加。"
        )
        max_stack = 1

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
        if not c.products:
            unusable.append(
                {**c.to_dict(), "reason": "该券未给出适用商品，无法自动判断，请人工确认"}
            )
            continue
        matches = match_coupon_products(c, cart_items)
        if not matches:
            unusable.append(
                {
                    **c.to_dict(),
                    "reason": "本单没有该券适用的商品",
                    "missing_products": c.product_names,
                }
            )
            continue
        covered = [m["productCode"] for m in matches]
        usable.append(
            {
                "coupon": c,
                "covered_codes": covered,
                "matches": matches,
                "covered_value_cents": sum(cart_value.get(x, 0) for x in covered),
                "confidence": min(m["confidence"] for m in matches),
            }
        )

    # 覆盖金额降序：覆盖越贵的商品越可能省得多（弱启发式，仅用于排序与剪枝）
    usable.sort(key=lambda x: (-x["covered_value_cents"], -x["confidence"]))

    # 名称推断出来的匹配必须显式告警，提醒 LLM 用 calculate-price 复核
    inferred = [u for u in usable if u["confidence"] < 1.0]
    if inferred:
        warnings.append(
            "有 {} 张券未能按 productCode 精确命中，已用商品名推断匹配"
            "（见 usable[].matched_cart_items），请务必用 calculate-price 试算确认。".format(
                len(inferred)
            )
        )

    # 券商品编码 ≠ 购物车编码：该券代表的是「券专属商品形态」，用它会改变购物车内容
    replacing = [u for u in usable if any(not m["same_code"] for m in u["matches"])]
    if replacing:
        warnings.append(
            "有 {} 张券的商品编码与购物车不一致（券用的是活动虚拟商品编码，如 99xxxxxxxx）。"
            "这类券**必须**用 apply[].productCode（券编码）去算价，"
            "而它对应的是「券的商品形态」——例如「中薯条买一送一」= 2 份中薯条按 1 份计价，"
            "与用户报单的「中薯条 x1」并不是同一件事，下单前务必与用户确认。".format(
                len(replacing)
            )
        )

    # ---- 候选生成 ---- #
    # 每个候选 = 1 张券，外加一个「不使用券」基线。
    # **没有「券叠加」候选**——实测 calculate-price 一次只接受 1 张券。
    candidates: list[dict] = [{"label": "不使用优惠券（基线）", "apply": [], "_v": 0, "_n": 0}]

    for u in usable:
        c: Coupon = u["coupon"]
        candidates.append(
            {
                "label": c.name,
                "apply": [
                    {
                        # ⚠️ 必须传**券自己的** productCode。实测：把「中薯条买一送一」
                        # 挂到菜单里的中薯条(4810) 上 → 600012「促销规则不支持该商品」；
                        # 挂到券编码(9900012387) 上 → 成功。券商品编码不是别名，是真实入参。
                        "productCode": m["couponProductCode"] or m["productCode"],
                        "cartProductCode": m["productCode"],
                        "couponId": c.cid,
                        "couponCode": c.codes[0] if c.codes else "",
                        "match": m["match"],
                    }
                    for m in u["matches"]
                ],
                "_v": u["covered_value_cents"],
                "_n": 1,
            }
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
        "usable": [
            {
                **u["coupon"].to_dict(),
                "matched_cart_items": u["matches"],
                "confidence": u["confidence"],
            }
            for u in usable
        ],
        "unusable": unusable,
        "candidates": out,
        "skipped": skipped,
        "warnings": warnings,
        "next_step": (
            "对 candidates 中每个候选（含基线），把 apply[].productCode + couponId + couponCode "
            "组装进 calculate-price 的 items[] 实际调用试算，取 data.price（单位：分）最低者为最终方案。"
            "三条硬规则：① 本脚本不预测优惠金额，一切以 calculate-price 为准；"
            "② 一次只能挂 1 张券，传 2 张会返回 code=600022「暂不支持多张券使用」；"
            "③ apply[].productCode 是**券的商品编码**（算价必须用它），"
            "apply[].cartProductCode 是购物车商品编码（分账归属用它），"
            "两者不同时说明该券对应券专属商品形态，需与用户确认。"
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
