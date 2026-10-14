#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · 券组合候选生成器 (plan_coupons)

麦当劳 MCP 的 ``query-store-coupons`` / ``query-my-coupons`` 会返回一批券，
但「这堆券怎么组合最省」对普通用户是不透明的。

本脚本负责把候选空间**剪枝**成至多 N 个（默认 8 个）值得一试的组合，
再由 ``calculate-price`` 做权威试算，最终选出应付总价最低的方案。
这样既能逼近最优解，又把 MCP 调用量压在限流阈值以内。

设计要点
--------
- **容错解析**：MCP 券字段名可能随版本变化，本脚本用多候选键名 + 正则兜底抽取
  「面额 / 门槛 / 类型 / 是否可叠加」，抽不到的字段记入 ``unparsed`` 交给 LLM 兜底。
- **候选剪枝**：门槛过滤 → 单券候选 → 可叠加券的组合（≤3 张）→ 按理论优惠降序取 TopK。
- **凑单提示**：券门槛略高于当前小计时，给出「再加 X 元可减 Y 元」的建议。

本脚本**不联网、不消耗 MCP 配额**。
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Sequence

from split_bill import cents_to_yuan, yuan_to_cents

MAX_COMBOS = 200  # 组合枚举上限，防止爆炸

RE_NUM = re.compile(r"(\d+(?:\.\d+)?)")
RE_DISCOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*折")


@dataclass
class Coupon:
    cid: str
    name: str
    amount_cents: int = 0
    threshold_cents: int = 0
    kind: str = "unknown"  # full_reduction | discount | item | unknown
    stackable: bool | None = None
    raw: dict = field(default_factory=dict)

    def theoretical_saving(self, subtotal_cents: int) -> int:
        """理论优惠额（乐观估计，最终以 calculate-price 为准）。"""
        if self.kind == "full_reduction":
            return self.amount_cents if subtotal_cents >= self.threshold_cents else 0
        if self.kind == "discount":
            return 0  # 折扣率未知，交由 calculate-price 判定
        if self.kind == "item":
            return self.amount_cents if subtotal_cents >= self.threshold_cents else 0
        return self.amount_cents if subtotal_cents >= self.threshold_cents else 0

    def eligible(self, subtotal_cents: int) -> bool:
        return subtotal_cents >= self.threshold_cents

    def to_dict(self) -> dict:
        return {
            "id": self.cid,
            "name": self.name,
            "amount": cents_to_yuan(self.amount_cents),
            "threshold": cents_to_yuan(self.threshold_cents),
            "kind": self.kind,
            "stackable": self.stackable,
        }


# --------------------------------------------------------------------------- #
# 容错解析
# --------------------------------------------------------------------------- #
_ID_KEYS = ("couponId", "coupon_id", "id", "code", "couponCode", "activityId")
_NAME_KEYS = ("couponName", "name", "title", "couponTitle", "activityName", "desc", "description")
_AMOUNT_KEYS = ("discountAmount", "amount", "value", "faceValue", "parValue", "reduceAmount", "couponAmount")
_THRESHOLD_KEYS = ("threshold", "minAmount", "useCondition", "limitAmount", "conditionAmount", "minConsume", "fullAmount")
_STACK_KEYS = ("stackable", "canStack", "isStackable", "canSuperimpose", "superposable")


def _first(raw: dict, keys: Sequence[str]):
    for k in keys:
        if k in raw and raw[k] not in (None, ""):
            return raw[k]
    return None


def _extract_amount(raw: dict) -> tuple[int, bool]:
    """返回 (面额分, 是否成功)。"""
    v = _first(raw, _AMOUNT_KEYS)
    if v is not None:
        try:
            return yuan_to_cents(v), True
        except (TypeError, ValueError):
            pass
    # 从文案里抠「减20」「立减 15.5」
    for k in _NAME_KEYS:
        text = str(raw.get(k, ""))
        m = re.search(r"减\s*(\d+(?:\.\d+)?)", text)
        if m:
            return yuan_to_cents(m.group(1)), True
    return 0, False


def _extract_threshold(raw: dict) -> int:
    v = _first(raw, _THRESHOLD_KEYS)
    if v is not None:
        try:
            return yuan_to_cents(v)
        except (TypeError, ValueError):
            pass
    for k in _NAME_KEYS:
        text = str(raw.get(k, ""))
        m = re.search(r"满\s*(\d+(?:\.\d+)?)", text)
        if m:
            return yuan_to_cents(m.group(1))
    return 0


def _extract_kind(raw: dict, name: str) -> str:
    text = f"{raw.get('type', '')} {raw.get('couponType', '')} {name}"
    if RE_DISCOUNT.search(text) or "折扣" in text:
        return "discount"
    if "满" in text or "减" in text:
        return "full_reduction"
    if any(k in text for k in ("单品", "指定商品", "兑换", "买一送一", "第二份")):
        return "item"
    return "unknown"


def normalize_coupons(raw_list: Sequence[dict]) -> tuple[list[Coupon], list[dict]]:
    coupons: list[Coupon] = []
    unparsed: list[dict] = []

    for i, raw in enumerate(raw_list or []):
        if not isinstance(raw, dict):
            unparsed.append({"index": i, "raw": raw, "reason": "非对象结构"})
            continue
        cid = str(_first(raw, _ID_KEYS) or f"coupon-{i}")
        name = str(_first(raw, _NAME_KEYS) or f"未命名券-{i}").strip()
        amount, ok_amount = _extract_amount(raw)
        threshold = _extract_threshold(raw)
        kind = _extract_kind(raw, name)
        stack_raw = _first(raw, _STACK_KEYS)
        stackable = None if stack_raw is None else bool(stack_raw)

        c = Coupon(
            cid=cid, name=name, amount_cents=amount,
            threshold_cents=threshold, kind=kind,
            stackable=stackable, raw=raw,
        )
        coupons.append(c)

        if not ok_amount and kind != "discount":
            unparsed.append({"id": cid, "name": name, "reason": "未能抽出面额，需 LLM 兜底"})

    return coupons, unparsed


# --------------------------------------------------------------------------- #
# 候选生成
# --------------------------------------------------------------------------- #
def plan(
    raw_coupons: Sequence[dict],
    subtotal_cents: int,
    top_k: int = 8,
    max_stack: int = 3,
) -> dict:
    coupons, unparsed = normalize_coupons(raw_coupons)
    warnings: list[str] = []

    eligible = [c for c in coupons if c.eligible(subtotal_cents)]
    ineligible = [c for c in coupons if not c.eligible(subtotal_cents)]

    # ---- 候选组合 ---- #
    combos: list[tuple[tuple[Coupon, ...], int]] = [((), 0)]  # 基线：不用券

    for c in eligible:
        combos.append(((c,), c.theoretical_saving(subtotal_cents)))

    # 可叠加的券才参与组合。stackable 为 None 时保守起见也纳入（交由试算裁决）
    stackable = [c for c in eligible if c.stackable is not False]
    for size in range(2, max_stack + 1):
        if len(stackable) < size:
            break
        for combo in itertools.combinations(stackable, size):
            if len(combos) >= MAX_COMBOS:
                warnings.append(f"组合数达到上限 {MAX_COMBOS}，已截断，结果可能非全局最优。")
                break
            saving = sum(c.theoretical_saving(subtotal_cents) for c in combo)
            combos.append((combo, saving))
        if len(combos) >= MAX_COMBOS:
            break

    # ---- 排序去重 ---- #
    combos.sort(key=lambda x: (-x[1], len(x[0])))
    seen: set[tuple[str, ...]] = set()
    candidates: list[dict] = []
    for combo, saving in combos:
        key = tuple(sorted(c.cid for c in combo))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "label": " + ".join(c.name for c in combo) if combo else "不使用优惠券",
                "coupon_ids": [c.cid for c in combo],
                "theoretical_saving": cents_to_yuan(saving),
                "_saving_cents": saving,
            }
        )
        if len(candidates) >= top_k:
            break

    # ---- 凑单提示 ---- #
    near_miss: list[dict] = []
    for c in ineligible:
        gap = c.threshold_cents - subtotal_cents
        if gap <= 0:
            continue
        gain = c.amount_cents - gap
        near_miss.append(
            {
                "coupon": c.name,
                "gap": cents_to_yuan(gap),
                "hint": (
                    f"再加 ¥{cents_to_yuan(gap)} 可用「{c.name}」"
                    + (f"，净省 ¥{cents_to_yuan(gain)}" if gain > 0 else "（净收益为负，不建议凑单）")
                ),
                "_gain_cents": gain,
            }
        )
    near_miss.sort(key=lambda x: -x["_gain_cents"])
    near_miss = [x for x in near_miss if x["_gain_cents"] > 0][:3]
    for x in near_miss:
        x.pop("_gain_cents", None)

    if not eligible:
        warnings.append("当前小计下没有任何券满足门槛，建议直接下单或参考凑单提示。")

    for c in candidates:
        c.pop("_saving_cents", None)

    return {
        "subtotal": cents_to_yuan(subtotal_cents),
        "coupon_total": len(coupons),
        "eligible": len(eligible),
        "candidates": candidates,
        "near_miss": near_miss,
        "unparsed": unparsed,
        "warnings": warnings,
        "next_step": (
            "请对 candidates 中的每个组合调用 MCP 工具 calculate-price 试算，"
            "取应付总价最低者为最终方案。"
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="plan_coupons",
        description="麦麦拼单官 · 券组合候选生成器（本地运行，不消耗 MCP 配额）",
    )
    p.add_argument("--input", "-i", help="输入 JSON 文件（含 coupons 与 subtotal）；缺省从 stdin 读取")
    p.add_argument("--top-k", type=int, default=8, help="返回的候选组合数上限（默认 8）")
    p.add_argument("--max-stack", type=int, default=3, help="单次组合最多叠加的券数（默认 3）")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        payload = json.load(sys.stdin)

    subtotal_cents = yuan_to_cents(payload.get("subtotal", 0))
    result = plan(
        raw_coupons=payload.get("coupons", []),
        subtotal_cents=subtotal_cents,
        top_k=args.top_k,
        max_stack=args.max_stack,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
