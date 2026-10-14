#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · 「随便」报单指派器 (assign_any)

群里总有人只回一句「随便」。这类报单没有具体餐品，硬去匹配菜单只会误伤，
所以流程上单独摘出来，由本脚本指派一个**具体餐品**。

默认指派「汉堡」，且遵循两条规则：

1. **尽量不同口味**：第 k 个「随便」拿候选池里的第 ``k % N`` 个汉堡，
   保证同一单里几个人不会拿到同一种汉堡。
2. **避开已点的**：如果某人已经明确点了巨无霸，其他人「随便」时优先跳过巨无霸。
   只有当候选池被排除空了才回退到全量。

指派是**确定性**的（按菜单顺序轮换，不引入随机数），因此同一份输入永远得到
同一份结果，便于复现与测试。

输入是 ``match_menu.py`` 的输出（``items`` 里带 ``intent`` 的条目会被替换），
输出是替换后的 ``items`` 与一份 ``assignments`` 指派记录（用于向用户展示）。

本脚本**不联网、不消耗 MCP 配额**。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Callable, Sequence

from match_menu import hamburger_candidates, normalize_menu
from split_bill import cents_to_yuan

# 品类 → (中文名, 候选筛选函数)
KINDS: dict[str, tuple[str, Callable[[Sequence[dict]], list[dict]]]] = {
    "burger": ("汉堡", hamburger_candidates),
}

# 备注里的忌口 → 需要排除的候选名称关键词。
# 刻意只覆盖最高频的几类，且宁可漏排除也不误排除（误排除会让「随便」无货可选）。
_AVOID_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("不要辣", "不吃辣", "别辣", "去辣", "无辣", "不加辣", "不要辣椒"), ("辣",)),
    (("不要牛", "不吃牛", "不要牛肉", "不吃牛肉"), ("巨无霸", "吉士", "牛")),
    (("不要鱼", "不吃鱼", "不要海鲜"), ("麦香鱼", "鱼")),
)


def _build_confirm(assignments: list[dict], label: str, excluded: list[dict]) -> str:
    """生成一段可以直接念给用户看的确认话术（「列出来问」）。"""
    lines = [f"以下 {len(assignments)} 份{label}是根据「随便」自动安排的，请确认有没有不吃的："]
    for a in assignments:
        note = f"（备注：{a['note']}）" if a.get("note") else ""
        lines.append(f"  · {a['person']} → {a['name']} ¥{a['price']}{note}")
    names = [a["name"] for a in assignments]
    if len(names) > 1:
        if len(set(names)) == len(names):
            lines.append(f"共 {len(names)} 种口味，无重复。")
        else:
            lines.append("口味有重复（候选不足），如需更换请直接说。")
    if excluded:
        lines.append(
            f"（{'、'.join(m['name'] for m in excluded)}已按明确点单保留，未参与指派。）"
        )
    return "\n".join(lines)


def _avoided_by(note: str) -> tuple[str, ...]:
    """从备注里解析出需要回避的候选关键词。"""
    text = str(note or "")
    hints: list[str] = []
    for triggers, excludes in _AVOID_RULES:
        if any(t in text for t in triggers):
            hints.extend(excludes)
    return tuple(hints)


def assign(matched: dict, raw_menu, kind: str = "burger") -> dict:
    """把 ``match_menu`` 输出里 intent 条目指派为具体餐品。

    :param matched: ``match_menu.py`` 的输出（需含 ``items``）
    :param raw_menu: 门店菜单（``query-meals`` 原始响应或其归一形态）
    :param kind: 指派品类，见 ``KINDS``（默认 ``burger`` = 汉堡）
    """
    if kind not in KINDS:
        raise ValueError(f"不支持的指派品类：{kind}（可选：{', '.join(KINDS)}）")

    label, picker = KINDS[kind]
    menu = normalize_menu(raw_menu)
    pool = picker(menu)

    warnings: list[str] = []
    items = list(matched.get("items") or [])
    pending = [i for i in items if i.get("intent")]

    if not pending:
        return {
            "kind": kind,
            "kind_label": label,
            "assigned_count": 0,
            "pool": [],
            "excluded": [],
            "assignments": [],
            "items": items,
            "warnings": ["没有需要指派的泛称报单。"],
            "confirm_message": "",
            "next_step": "items 可直接进入分账流程。",
        }

    if not pool:
        return {
            "kind": kind,
            "kind_label": label,
            "assigned_count": 0,
            "pool": [],
            "excluded": [],
            "assignments": [],
            "items": items,
            "warnings": [
                f"菜单里没有识别到任何{label}候选，无法指派。"
                "请确认门店菜单是否已正确加载，或让报单人改为指定餐品。"
            ],
            "confirm_message": "",
            "next_step": "不要编造餐品，先与用户确认。",
        }

    # 已被明确点选的餐品编码：指派时优先避开，避免「撞口味」
    explicit: set[str] = set()
    for i in items:
        if i.get("intent"):
            continue
        code = i.get("matched_code")
        if code:
            explicit.add(str(code))

    excluded = [m for m in pool if m["code"] in explicit]
    preferred = [m for m in pool if m["code"] not in explicit]
    if not preferred:
        # 所有汉堡都被点过了，只能回退到全量池
        preferred = pool
        excluded = []
        warnings.append(
            f"候选{label}已被明确点单占满，指派将从头轮换，可能与已点口味重复。"
        )

    def brief(m: dict) -> dict:
        return {
            "code": m["code"],
            "name": m["name"],
            "price": cents_to_yuan(m["price_cents"]),
        }

    assignments: list[dict] = []
    out_items: list[dict] = []
    # 先发「还没人拿过的」，发完了再从头轮换——让「随便」的人尽量不重样
    remaining = list(preferred)
    k = 0
    conflicts: list[str] = []

    def pick(avoid: tuple[str, ...]) -> dict:
        """挑一个候选：先未分配且不忌口的，再轮换中不忌口的，最后只能硬来。"""
        nonlocal k

        def ok(m: dict) -> bool:
            return not any(h in m["name"] for h in avoid)

        for idx, m in enumerate(remaining):
            if ok(m):
                return remaining.pop(idx)
        for _ in range(len(preferred)):
            t = preferred[k % len(preferred)]
            k += 1
            if ok(t):
                return t
        # 候选全被忌口排除，只能回退（并告警）
        fallback = preferred[k % len(preferred)]
        k += 1
        return fallback

    for i in items:
        if not i.get("intent"):
            out_items.append(i)
            continue

        avoid = _avoided_by(i.get("note", ""))
        qty = int(i.get("qty") or 1)
        # 一个人报「随便 x2」→ 拆成 2 个不同口味的汉堡
        for _ in range(qty):
            target = pick(avoid)
            if avoid and any(h in target["name"] for h in avoid):
                conflicts.append(
                    f"{i.get('person', '')} 备注「{i.get('note', '')}」，"
                    f"但候选里已无符合的{label}，只能指派 {target['name']}"
                )
            out_items.append(
                {
                    "person": i.get("person", ""),
                    "query": i.get("query", ""),
                    "qty": 1,
                    "note": i.get("note", ""),
                    "intent": i["intent"],
                    "matched_code": target["code"],
                    "matched_name": target["name"],
                    "unit_price": cents_to_yuan(target["price_cents"]),
                    "confidence": 1.0,
                    "assigned": True,
                    "reason": f"由「{i.get('query', '')}」指派为 {target['name']}",
                }
            )
            assignments.append(
                {
                    "person": i.get("person", ""),
                    "from": i.get("query", ""),
                    **brief(target),
                    "note": i.get("note", ""),
                }
            )

    if len(assignments) > 1:
        names = [a["name"] for a in assignments]
        if len(set(names)) == len(names):
            warnings.append(
                f"{len(assignments)} 位「随便」已指派为 {len(set(names))} 种不同口味。"
                "请把指派结果展示给用户确认（尤其忌口与偏好）。"
            )
        else:
            warnings.append(
                "指派结果存在重复口味（候选数量少于「随便」人数）。"
                "请把结果展示给用户确认。"
            )

    if conflicts:
        warnings.extend(conflicts)
        warnings.append("上述条目的忌口未能满足，请务必与用户确认后再下单。")

    return {
        "kind": kind,
        "kind_label": label,
        "assigned_count": len(assignments),
        "pool": [brief(m) for m in pool],
        "excluded": [brief(m) for m in excluded],
        "assignments": assignments,
        "items": out_items,
        "warnings": warnings,
        # 直接可用的确认话术：把这段「列出来问」用户，逐条列出谁拿到了什么
        "confirm_message": _build_confirm(assignments, label, excluded),
        "next_step": (
            "把 confirm_message（或 assignments）**逐条列出**给用户确认；"
            "确认后 items 进入 from_price.py / split_bill.py 分账流程。"
            "注意：若用户不吃指派的口味，改为让他自己指定，不要重复指派。"
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="assign_any",
        description="麦麦拼单官 · 把「随便」类报单指派为具体餐品（默认汉堡，尽量不重样）",
    )
    p.add_argument("--input", "-i", help="match_menu 的输出 JSON；缺省从 stdin 读取")
    p.add_argument("--menu", "-m", help="门店菜单 JSON；缺省取输入文件的 menu 字段")
    p.add_argument(
        "--kind",
        default="burger",
        choices=sorted(KINDS),
        help="指派品类（默认 burger = 汉堡）",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        payload = json.load(sys.stdin)

    menu = payload.get("menu")
    if args.menu:
        with open(args.menu, "r", encoding="utf-8") as fh:
            menu = json.load(fh)
    if menu is None:
        print("缺少菜单：请用 --menu 指定，或在输入 JSON 里带 menu 字段。", file=sys.stderr)
        return 2

    result = assign(matched=payload, raw_menu=menu, kind=args.kind)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
