#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · 菜单匹配器 (match_menu)

群聊里大家报的是口语化的名字（「麦辣鸡腿堡套餐」「中薯」），
而 ``create-order`` 需要的是**餐品编码**。本脚本负责把前者映射到后者。

匹配策略（由强到弱）
--------------------
1. 完全一致
2. 去掉空格/标点/别名归一后一致
3. 包含关系（报单名 ⊂ 菜单名 或反之）
4. 字符级相似度（``difflib.SequenceMatcher``）≥ 阈值（默认 0.6）
5. 仍未命中的，标记为 ``unmatched`` 交回 LLM 判断，不擅自猜测

别名表内置了常见简称（中薯/大薯/可乐/麦乐鸡 …），可自行扩展。

本脚本**不联网、不消耗 MCP 配额**。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from typing import Sequence

from split_bill import cents_to_yuan, yuan_to_cents

ALIASES = {
    "中薯": "中号薯条",
    "大薯": "大号薯条",
    "小薯": "小号薯条",
    "可乐": "可口可乐",
    "零度": "零度可乐",
    "雪碧": "雪碧",
    "麦乐鸡": "麦乐鸡",
    "麦辣鸡腿堡": "麦辣鸡腿堡",
    "板烧": "板烧鸡腿堡",
    "巨无霸": "巨无霸",
    "麦香鱼": "麦香鱼",
    "吉士": "双层吉士汉堡",
    "鸡翅": "麦辣鸡翅",
}

_NAME_KEYS = ("name", "mealName", "productName", "title", "dishName", "餐品名称", "名称")
_CODE_KEYS = ("code", "mealCode", "productCode", "skuId", "id", "餐品编码", "编码")
# currentPrice 在前：菜单里它才是实际售价，originalPrice 是划线价
_PRICE_KEYS = ("currentPrice", "salePrice", "price", "amount", "价格", "售价")

RE_PUNCT = re.compile(r"[\s\-_·．.、,，/()（）\[\]【】]+")


def _first(raw: dict, keys: Sequence[str]):
    for k in keys:
        if k in raw and raw[k] not in (None, ""):
            return raw[k]
    return None


def _norm(text: str) -> str:
    t = str(text or "")
    t = ALIASES.get(t.strip(), t)
    return RE_PUNCT.sub("", t).lower()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _price_cents(item: dict) -> int:
    v = _first(item, _PRICE_KEYS)
    if v is None:
        return 0
    try:
        return yuan_to_cents(v)
    except (TypeError, ValueError):
        return 0


def _entry(code: str, item: dict) -> dict | None:
    name = str(_first(item, _NAME_KEYS) or "").strip()
    if not name:
        return None
    return {
        "code": str(_first(item, _CODE_KEYS) or code),
        "name": name,
        "price_cents": _price_cents(item),
        "category": "",
        "tags": [],
        "raw": item,
    }


def normalize_menu(raw) -> list[dict]:
    """把菜单输入归一为 ``[{code, name, price_cents}]``。

    兼容三种真实形态：

    1. **MCP ``query-meals`` 原始响应**（实测格式）::

           {"data": {"meals": {"1100": {"name": "巨无霸", "currentPrice": "25.5"}}}}

       注意 ``data.meals`` 是 **以餐品编码为 key 的字典**，不是数组；
       且条目内部没有 ``code`` 字段，编码取自 map 的 key。
       分类 ``data.categories[].meals[]`` 只有 ``code`` 和 ``tags``，没有名称，
       所以必须以 ``data.meals`` 为准。

    2. **``{code: item}`` 字典**（调用方自行裁剪后的形态）。

    3. **数组** ``[{code, name, price}, ...]``（人工整理或其它数据源）。
    """
    menu: list[dict] = []

    # 形态 3：数组
    if isinstance(raw, list):
        for i, item in enumerate(raw):
            if isinstance(item, dict):
                e = _entry(f"item-{i}", item)
                if e:
                    menu.append(e)
        return menu

    if not isinstance(raw, dict):
        return menu

    # 形态 1：剥掉 data / result 外层
    node = raw
    for key in ("data", "result"):
        nxt = node.get(key)
        if isinstance(nxt, dict):
            node = nxt

    # 分类信息只存在于 data.categories[].meals[]，data.meals 里没有，需先建映射。
    # 「巨无霸」这类名字里没有「堡」字，只能靠分类名「巨无霸 牛鱼肉堡」认出来。
    cat_of: dict[str, tuple[str, list]] = {}
    categories = node.get("categories")
    if isinstance(categories, list):
        for c in categories:
            if not isinstance(c, dict):
                continue
            cname = str(c.get("name") or "").replace("\n", " ").strip()
            for cm in c.get("meals") or []:
                if isinstance(cm, dict) and cm.get("code") is not None:
                    cat_of[str(cm["code"])] = (cname, list(cm.get("tags") or []))

    meals = node.get("meals")
    if isinstance(meals, dict):
        for code, item in meals.items():
            if isinstance(item, dict):
                e = _entry(str(code), item)
                if e:
                    cname, tags = cat_of.get(str(code), ("", []))
                    e["category"] = cname
                    e["tags"] = tags or list(item.get("tags") or [])
                    menu.append(e)
        if menu:
            return menu

    # 形态 2：直接是 {code: item}
    for code, item in raw.items():
        if isinstance(item, dict):
            e = _entry(str(code), item)
            if e:
                menu.append(e)
    return menu


# ---- 汉堡识别（供 assign_any.py 指派「随便」类报单） ---- #
# **分类名优先**：真实菜单里「巨无霸」被放在「巨无霸 牛鱼肉堡」分类下，
# 名字里并没有「堡」字，只靠名称关键词会漏掉它。
BURGER_CATEGORY_HINT = "堡"
BURGER_NAME_HINTS = ("堡", "巨无霸", "麦香鸡", "麦香鱼", "安格斯", "培根")
# 套餐/组合不算「一个汉堡」，指派时应排除
_COMBO_HINTS = ("套餐", "三件套", "四件套", "五件套", "组合", "分享", "双人", "多人", "家庭", "桶")


def is_burger(entry: dict) -> bool:
    """判断菜单项是否为可单独指的汉堡单品。"""
    name = str(entry.get("name") or "")
    if any(h in name for h in _COMBO_HINTS):
        return False
    if BURGER_CATEGORY_HINT in str(entry.get("category") or ""):
        return True
    return any(h in name for h in BURGER_NAME_HINTS)


def hamburger_candidates(menu: Sequence[dict]) -> list[dict]:
    """从菜单里筛出汉堡候选，保持菜单原有顺序（确定性，便于复现指派结果）。"""
    return [m for m in menu if is_burger(m)]


def match_one(query: str, menu: Sequence[dict], threshold: float = 0.6) -> dict:
    """把单个报单名匹配到菜单项。"""
    q_raw = str(query).strip()
    q = _norm(q_raw)
    if not q:
        return {"query": q_raw, "matched": None, "confidence": 0.0, "reason": "空名称"}

    # 1) 完全一致
    for m in menu:
        if _norm(m["name"]) == q:
            return {"query": q_raw, "matched": m, "confidence": 1.0, "reason": "完全一致"}

    # 2) 包含关系
    contains = [m for m in menu if q in _norm(m["name"]) or _norm(m["name"]) in q]
    if contains:
        best = min(contains, key=lambda m: abs(len(_norm(m["name"])) - len(q)))
        return {"query": q_raw, "matched": best, "confidence": 0.85, "reason": "包含匹配"}

    # 3) 字符集包含：处理词序颠倒（「麦旋风奥利奥」vs「奥利奥麦旋风」）
    if len(q) >= 2:
        q_chars = set(q)
        subset = [m for m in menu if q_chars <= set(_norm(m["name"]))]
        if subset:
            best = min(subset, key=lambda m: abs(len(_norm(m["name"])) - len(q)))
            return {"query": q_raw, "matched": best, "confidence": 0.8, "reason": "字符集匹配（词序不同）"}

    # 4) 相似度
    scored = [(_similarity(q, _norm(m["name"])), m) for m in menu]
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] >= threshold:
        return {"query": q_raw, "matched": scored[0][1], "confidence": round(scored[0][0], 3), "reason": "相似度匹配"}

    return {
        "query": q_raw,
        "matched": None,
        "confidence": round(scored[0][0], 3) if scored else 0.0,
        "reason": "未达阈值，需人工/LLM 确认",
        "suggestions": [m["name"] for _, m in scored[:3]],
    }


def match_orders(orders: Sequence[dict], raw_menu: Sequence[dict], threshold: float = 0.6) -> dict:
    """批量匹配：orders 为 parse_orders.py 的 orders 字段。"""
    menu = normalize_menu(raw_menu)
    results: list[dict] = []
    unmatched: list[dict] = []

    for o in orders:
        person = o.get("person", "")
        for it in o.get("items", []):
            # 「随便」类泛称没有具体餐品，硬匹配只会误伤 → 直接转交 assign_any.py
            if it.get("intent"):
                entry = {
                    "person": person,
                    "query": it.get("name", ""),
                    "qty": it.get("qty", 1),
                    "note": it.get("note", ""),
                    "intent": it["intent"],
                    "matched_code": None,
                    "matched_name": None,
                    "unit_price": "0.00",
                    "confidence": 0.0,
                    "reason": f"报单为「{it.get('name', '')}」（泛称/不挑），需由 assign_any 指派",
                }
                results.append(entry)
                unmatched.append(entry)
                continue

            r = match_one(it.get("name", ""), menu, threshold)
            entry = {
                "person": person,
                "query": it.get("name", ""),
                "qty": it.get("qty", 1),
                "note": it.get("note", ""),
                "matched_code": (r["matched"] or {}).get("code"),
                "matched_name": (r["matched"] or {}).get("name"),
                "unit_price": cents_to_yuan((r["matched"] or {}).get("price_cents", 0)),
                "confidence": r["confidence"],
                "reason": r["reason"],
            }
            if r.get("suggestions"):
                entry["suggestions"] = r["suggestions"]
            results.append(entry)
            if not r["matched"]:
                unmatched.append(entry)

    return {
        "menu_size": len(menu),
        "matched_count": len(results) - len(unmatched),
        "unmatched_count": len(unmatched),
        "items": results,
        "unmatched": unmatched,
        "next_step": (
            "对 unmatched 中的条目，请结合门店菜单与用户意图人工确认后补齐餐品编码；"
            "确认完毕后把 items 组装为 split_bill.py 所需的输入。"
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="match_menu",
        description="麦麦拼单官 · 报单名 → 菜单餐品编码 匹配器（本地运行）",
    )
    p.add_argument("--input", "-i", help="输入 JSON（含 orders 与 menu）；缺省从 stdin 读取")
    p.add_argument("--threshold", type=float, default=0.6, help="相似度阈值（默认 0.6）")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        payload = json.load(sys.stdin)

    result = match_orders(
        orders=payload.get("orders", []),
        raw_menu=payload.get("menu", []),
        threshold=args.threshold,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
