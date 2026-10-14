#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · 群聊报单解析器 (parse_orders)

把群里七嘴八舌的报单文本，解析成结构化 JSON，交给后续的菜单匹配与分账流程。

支持的真实写法举例
------------------
::

    @张三 麦辣鸡腿堡套餐 x1
    李四：巨无霸 + 中薯 + 可乐
    王五 2个麦香鱼 1份薯条
    @赵六 板烧鸡腿堡 不要生菜 少冰可乐
    我 1+1随心配
    【12:30】@张三 双层吉士汉堡×2

解析规则
--------
- **人**：``@昵称`` / ``昵称：`` / ``昵称`` 开头三种形式；``我``/``我点``/``我要`` 归一为 ``__ME__``。
- **数量**：``x2`` ``×2`` ``*2`` ``2个`` ``2份`` ``2杯``，以及中文数字 ``两个``。
- **备注**：``不要X`` ``去X`` ``少X`` ``加X`` ``多X`` ``换X`` ``无X``。
- **分隔**：``、`` ``,`` ``，`` ``/`` ``+``（数字之间的 ``+`` 不切，保护 ``1+1随心配``）。

本脚本**不联网、不消耗 MCP 配额**，纯本地文本处理。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Sequence

ME = "__ME__"

# 时间戳 / 系统消息，先剔除
RE_TS_BRACKET = re.compile(r"^\s*[\[\(【]?\s*\d{1,2}:\d{2}(?::\d{2})?\s*[\]\)】]?\s*")
RE_TS_FULL = re.compile(r"^\s*\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?\s*")
RE_NOISE = re.compile(r"^\s*[\[\(【]\s*(图片|表情|动画表情|语音|视频|链接|文件|位置)\s*[\]\)】]\s*$")

# 前缀：@昵称 / 昵称：
RE_AT = re.compile(r"^\s*[@＠]\s*([^\s：:，,、]{1,24})\s*")
RE_COLON = re.compile(r"^\s*([^\s：:，,、]{1,24})\s*[：:]\s*")

# 数量：后缀 x2 / ×2 / *2 / 2个 / 2份 ...；前缀 2个XXX
RE_QTY_SUFFIX = re.compile(r"\s*(?:[xX×*✕]\s*(\d+)|(\d+)\s*(?:个|份|杯|盒|套|块|只|支|袋|瓶))\s*$")
RE_QTY_PREFIX = re.compile(r"^\s*(?:(\d+)\s*(?:个|份|杯|盒|套|块|只|支|袋|瓶))\s*")

# 备注关键词。
# 采用**受控词表**而非泛化匹配，避免两类误伤：
#   1) 裸「无」会把「巨无霸」拆成「巨」+备注「无霸」——故不收录；
#   2) 泛化 `去[^\s]{1,3}` 会把「去冰可乐」整串吞掉——故限定为「去」+ 词表内元素。
_NOTE_ING = (
    "番茄酱|酸黄瓜|沙拉酱|生菜|洋葱|香菜|吸管|餐具|芝士"
    "|冰|糖|酱|菜|葱|盐|辣|奶|油|皮|盖"
)
RE_NOTE = re.compile(
    r"(不要(?:" + _NOTE_ING + r")"
    r"|去掉?(?:" + _NOTE_ING + r")"
    r"|少(?:" + _NOTE_ING + r")"
    r"|多加?(?:" + _NOTE_ING + r")"
    r"|换[^\s、,，/+]{1,4}"
    r"|备注[^\s、,，/+]{1,12})"
)

CN_NUM = {
    "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
RE_CN_QTY = re.compile(r"([一两二三四五六七八九十])\s*(?:个|份|杯|盒|套|块|只|支)")

# 分隔符：数字之间的 + 不切（保护 1+1随心配）；空格也作为分隔符（「巨无霸 中薯 可乐」）
RE_SPLIT = re.compile(r"[\s、,，/]|(?<!\d)[+＋](?!\d)")

# 纯数量片段：单独出现的 x2 / ×2 / *2（拆分后需回填给前一项）
RE_PURE_QTY = re.compile(r"^[xX×*✕]\s*(\d+)$")

# 有意义内容：至少含一个汉字/字母/数字，用于过滤纯标点与乱码片段
RE_MEANINGFUL = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def _norm_person(name: str) -> str:
    name = name.strip().strip(":：")
    if name in {"我", "我点", "我要", "我的", "自己"}:
        return ME
    return name


def _cn_to_int(text: str) -> str:
    """把『两个』『三份』这类中文数量替换成阿拉伯数字。"""
    def repl(m: re.Match) -> str:
        return f"{CN_NUM[m.group(1)]}"

    return RE_CN_QTY.sub(repl, text)


def _extract_qty(seg: str) -> tuple[str, int]:
    """从一段文本中抽取数量，返回 (去掉数量的名称, 数量)。"""
    seg = seg.strip()
    qty = 1

    m = RE_QTY_SUFFIX.search(seg)
    if m:
        qty = int(m.group(1) or m.group(2))
        seg = seg[: m.start()].strip()
        return seg, qty

    m = RE_QTY_PREFIX.match(seg)
    if m:
        qty = int(m.group(1))
        seg = seg[m.end():].strip()
        return seg, qty

    return seg, qty


def _extract_note(seg: str) -> tuple[str, str]:
    """抽取备注，返回 (去掉备注的名称, 备注)。"""
    notes = [m.group(1) for m in RE_NOTE.finditer(seg)]
    name = RE_NOTE.sub(" ", seg)
    name = re.sub(r"\s{2,}", " ", name).strip()
    return name, " ".join(notes)


def parse_line(line: str) -> dict | None:
    """解析单行报单。无法识别返回 None。"""
    raw = line.rstrip("\n")
    if not raw.strip():
        return None
    if RE_NOISE.match(raw):
        return None

    s = raw
    s = RE_TS_FULL.sub("", s)
    s = RE_TS_BRACKET.sub("", s)
    s = s.strip()
    if not s:
        return None

    # ---- 抽人 ---- #
    person = None
    m = RE_AT.match(s)
    if m:
        person = _norm_person(m.group(1))
        rest = s[m.end():].strip()
    else:
        m = RE_COLON.match(s)
        if m:
            person = _norm_person(m.group(1))
            rest = s[m.end():].strip()
        else:
            # 退化：首个 token 视为人（需 ≥2 个 token 且首个 token 不含数字）
            parts = s.split(None, 1)
            if len(parts) == 2 and len(parts[0]) <= 12 and not re.search(r"\d", parts[0]):
                person = _norm_person(parts[0])
                rest = parts[1].strip()
            else:
                person = ME
                rest = s

    if not rest:
        return None

    # ---- 切分商品 ---- #
    rest = _cn_to_int(rest)
    segments = [x.strip() for x in RE_SPLIT.split(rest) if x.strip()]

    items: list[dict] = []
    for seg in segments:
        seg = seg.strip(" .。！!~～")
        if not seg:
            continue

        # 单独的 x2 / ×2：回填给前一项，而不是当成一个餐品
        m_pure = RE_PURE_QTY.match(seg)
        if m_pure:
            if items:
                items[-1]["qty"] = int(m_pure.group(1))
            continue

        name, qty = _extract_qty(seg)
        name, note = _extract_note(name)
        name = name.strip(" .。！!~～")

        if not name:
            # 只剩备注（如「不要番茄酱」）：挂到前一项上
            if note and items:
                items[-1]["note"] = (items[-1]["note"] + " " + note).strip()
            continue

        # 过滤纯标点/乱码片段（如 "???"、"..."）
        if not RE_MEANINGFUL.search(name):
            continue

        items.append({"name": name, "qty": qty, "note": note})

    if not items:
        return None
    return {"person": person, "items": items, "raw": raw.strip()}


def parse_orders(text: str) -> dict:
    """解析整段群聊文本，返回结构化报单。"""
    orders: list[dict] = []
    warnings: list[str] = []

    for line in text.splitlines():
        parsed = parse_line(line)
        if parsed:
            orders.append(parsed)

    if not orders:
        warnings.append("未识别到任何报单，请检查格式（示例：@张三 麦辣鸡腿堡套餐 x1）")

    # 按人合并
    merged: dict[str, dict] = {}
    order_keys: list[str] = []
    for o in orders:
        p = o["person"]
        if p not in merged:
            merged[p] = {"person": p, "items": [], "raw_lines": []}
            order_keys.append(p)
        merged[p]["items"].extend(o["items"])
        merged[p]["raw_lines"].append(o["raw"])

    if ME in merged:
        warnings.append(
            f"检测到 {len(merged[ME]['items'])} 条以『我』开头的报单，"
            "已归一为 __ME__，请替换为实际姓名。"
        )

    for p, info in merged.items():
        for it in info["items"]:
            if not it["name"]:
                warnings.append(f"{p} 的一条报单未能解析出餐品名，请人工确认。")

    return {
        "people": order_keys,
        "count": len(order_keys),
        "item_count": sum(len(v["items"]) for v in merged.values()),
        "orders": [merged[k] for k in order_keys],
        "warnings": warnings,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="parse_orders",
        description="麦麦拼单官 · 群聊报单解析器（本地运行，不消耗 MCP 配额）",
    )
    p.add_argument("--input", "-i", help="报单文本文件路径；缺省从 stdin 读取")
    p.add_argument("--pretty", action="store_true", help="以人类可读的表格形式输出")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()

    result = parse_orders(text)

    if args.pretty:
        print(f"识别到 {result['count']} 人 / {result['item_count']} 项")
        print("-" * 46)
        for o in result["orders"]:
            person = "（我）" if o["person"] == ME else o["person"]
            for it in o["items"]:
                note = f"  [{it['note']}]" if it["note"] else ""
                print(f"  {person:<10} {it['name']} ×{it['qty']}{note}")
        for w in result["warnings"]:
            print(f"  ⚠ {w}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
