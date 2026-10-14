#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · 分账引擎 (split_bill)

设计目标
--------
1. **零浮点误差**：金额全程以「分」为单位的整数运算，不出现 0.1 + 0.2 类问题。
2. **总额恒等**：分摊使用「最大余额法」(Largest Remainder Method / Hamilton 法)，
   先按权重取整，再把取整产生的余数按小数部分从大到小逐一分配。
   数学保证 ``sum(每人分摊额) == 待分摊总额``，永不出现 0.01 的尾差。
3. **口径可选**：把拼单群里最容易吵架的「优惠怎么分」显式化为选项，而不是争议点：
   - ``proportional`` 按商品原价比例分摊（默认，买得多省得多，最公平）
   - ``equal``        按人头均摊（最省事，适合大家都差不多的情况）
   - ``owner``        谁触发/持有该券谁独享优惠（券是人家的，人情归人家）
4. **上界保护**：优惠分摊不会超过本人商品原价，避免出现「负的应付金额」。

用法
----
::

    python split_bill.py --input bill.json
    python split_bill.py --input bill.json --markdown
    cat bill.json | python split_bill.py

输入 JSON 结构见 ``references/split-algorithm.md``。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from typing import Iterable, Sequence

DISCOUNT_POLICIES = ("proportional", "equal", "owner")
FEE_POLICIES = ("equal", "proportional")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def yuan_to_cents(value: object) -> int:
    """把「元」转成「分」的整数。支持 int / float / 数字字符串。"""
    if value is None or value == "":
        return 0
    return int(round(float(value) * 100))


def cents_to_yuan(cents: int) -> str:
    """把「分」格式化为两位小数的「元」字符串。"""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def allocate(total_cents: int, weights: Sequence[float]) -> list[int]:
    """最大余额法：把 ``total_cents`` 按 ``weights`` 比例分摊为整数分列表。

    保证 ``sum(返回值) == total_cents``（在 ``total_cents >= 0`` 且权重非负时）。
    """
    n = len(weights)
    if n == 0:
        return []
    if total_cents == 0:
        return [0] * n

    w = [max(0.0, float(x)) for x in weights]
    total_weight = sum(w)
    if total_weight <= 0:
        # 权重全为 0 时退化为均分
        w = [1.0] * n
        total_weight = float(n)

    raw = [total_cents * x / total_weight for x in w]
    base = [int(math.floor(r)) for r in raw]

    remainder = total_cents - sum(base)
    # 小数部分降序；平局按索引升序，保证结果可复现（不依赖 dict/set 顺序）
    order = sorted(range(n), key=lambda i: (-(raw[i] - math.floor(raw[i])), i))
    k = 0
    while remainder > 0:
        base[order[k % n]] += 1
        remainder -= 1
        k += 1

    return base


def allocate_capped(
    total_cents: int,
    weights: Sequence[float],
    caps: Sequence[int | None] | None = None,
) -> tuple[list[int], int]:
    """带单人上限的按权重分摊（注水法 / water-filling）。

    返回 ``(分摊列表, 未能分摊的溢出额)``。溢出额通常为 0；
    仅当所有人都触及上限时才会 > 0（例如优惠总额超过了商品总额）。
    """
    n = len(weights)
    if n == 0:
        return [], total_cents
    if caps is None:
        caps = [None] * n

    result = [0] * n
    remaining = total_cents
    active = list(range(n))

    while remaining > 0 and active:
        sub_weights = [max(0.0, float(weights[i])) for i in active]
        sub_caps = [None if caps[i] is None else caps[i] - result[i] for i in active]
        shares = allocate(remaining, sub_weights)

        progressed = False
        for pos, i in enumerate(active):
            give = shares[pos]
            if sub_caps[pos] is not None:
                give = min(give, sub_caps[pos])
            if give > 0:
                progressed = True
                result[i] += give
                remaining -= give

        if not progressed:
            # 所有人都已触顶，剩余部分无法分摊
            break
        active = [
            i for i in active if caps[i] is None or result[i] < caps[i]
        ]

    return result, remaining


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
@dataclass
class LineItem:
    """一条商品行。"""

    person: str
    name: str
    unit_price_cents: int
    qty: int = 1

    @property
    def subtotal_cents(self) -> int:
        return self.unit_price_cents * self.qty

    @classmethod
    def from_dict(cls, d: dict) -> "LineItem":
        return cls(
            person=str(d["person"]).strip(),
            name=str(d.get("name", "")).strip(),
            unit_price_cents=yuan_to_cents(d.get("unit_price", d.get("price", 0))),
            qty=int(d.get("qty", d.get("quantity", 1)) or 1),
        )


@dataclass
class PersonShare:
    """一个人的分账结果。"""

    person: str
    subtotal_cents: int
    discount_cents: int
    fee_cents: int
    payable_cents: int
    items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "person": self.person,
            "subtotal": cents_to_yuan(self.subtotal_cents),
            "discount": cents_to_yuan(self.discount_cents),
            "fee": cents_to_yuan(self.fee_cents),
            "payable": cents_to_yuan(self.payable_cents),
            "items": self.items,
        }


@dataclass
class SplitResult:
    """一次拼单的完整分账结果。"""

    shares: list[PersonShare]
    subtotal_cents: int
    discount_cents: int
    delivery_fee_cents: int
    packaging_fee_cents: int
    payable_cents: int
    discount_policy: str
    fee_policy: str
    payer: str | None = None
    overflow_cents: int = 0
    note: str = ""

    # ---- 校验 ---------------------------------------------------------- #
    def verify(self) -> None:
        """自检：分账明细之和必须恒等于订单应付总额。"""
        s = sum(x.payable_cents for x in self.shares)
        if s != self.payable_cents:
            raise AssertionError(
                f"分账不自洽：明细合计 {cents_to_yuan(s)} != 应付总额 "
                f"{cents_to_yuan(self.payable_cents)}"
            )
        expected = (
            self.subtotal_cents
            - self.discount_cents
            + self.delivery_fee_cents
            + self.packaging_fee_cents
        )
        if expected != self.payable_cents:
            raise AssertionError(
                f"订单不自洽：小计-优惠+费用 = {cents_to_yuan(expected)} != "
                f"{cents_to_yuan(self.payable_cents)}"
            )

    # ---- 输出 ---------------------------------------------------------- #
    def transfers(self) -> list[dict]:
        """收款方案：其余人各自应付给垫付人（星型拓扑，转账笔数最少）。"""
        if not self.payer:
            return []
        return [
            {
                "from": x.person,
                "to": self.payer,
                "amount": cents_to_yuan(x.payable_cents),
            }
            for x in self.shares
            if x.person != self.payer and x.payable_cents > 0
        ]

    def to_dict(self) -> dict:
        return {
            "policy": {
                "discount": self.discount_policy,
                "fee": self.fee_policy,
            },
            "summary": {
                "subtotal": cents_to_yuan(self.subtotal_cents),
                "discount": cents_to_yuan(self.discount_cents),
                "delivery_fee": cents_to_yuan(self.delivery_fee_cents),
                "packaging_fee": cents_to_yuan(self.packaging_fee_cents),
                "payable": cents_to_yuan(self.payable_cents),
                "saved": cents_to_yuan(self.discount_cents),
                "payer": self.payer,
                "overflow": cents_to_yuan(self.overflow_cents),
            },
            "shares": [x.to_dict() for x in self.shares],
            "transfers": self.transfers(),
            "note": self.note,
        }

    def to_markdown(self) -> str:
        """生成可直接贴到群里的分账文本。"""
        lines: list[str] = []
        lines.append("## 🍟 拼单分账单")
        lines.append("")
        if self.note:
            lines.append(f"> {self.note}")
            lines.append("")

        lines.append("| 谁 | 商品原价 | 分摊优惠 | 分摊费用 | **应付** |")
        lines.append("|---|---:|---:|---:|---:|")
        for x in self.shares:
            lines.append(
                f"| {x.person} | ¥{cents_to_yuan(x.subtotal_cents)} "
                f"| -¥{cents_to_yuan(x.discount_cents)} "
                f"| +¥{cents_to_yuan(x.fee_cents)} "
                f"| **¥{cents_to_yuan(x.payable_cents)}** |"
            )
        lines.append(
            f"| **合计** | ¥{cents_to_yuan(self.subtotal_cents)} "
            f"| -¥{cents_to_yuan(self.discount_cents)} "
            f"| +¥{cents_to_yuan(self.delivery_fee_cents + self.packaging_fee_cents)} "
            f"| **¥{cents_to_yuan(self.payable_cents)}** |"
        )
        lines.append("")

        policy_cn = {
            "proportional": "按商品原价比例分摊",
            "equal": "按人头均摊",
            "owner": "谁用券谁受益",
        }
        lines.append(
            f"**分账口径**：优惠 {policy_cn.get(self.discount_policy, self.discount_policy)}"
            f"；配送/打包费 {'按人头均摊' if self.fee_policy == 'equal' else '按原价比例分摊'}"
        )
        if self.discount_cents > 0:
            rate = self.discount_cents / self.subtotal_cents * 100 if self.subtotal_cents else 0
            lines.append(f"**本单共省**：¥{cents_to_yuan(self.discount_cents)}（约 {rate:.1f}%）")
        lines.append("")

        transfers = self.transfers()
        if transfers:
            lines.append("### 💸 转账")
            for t in transfers:
                lines.append(f"- **{t['from']}** → {t['to']}：¥{t['amount']}")
        elif self.payer:
            lines.append(f"无需转账（本单由 {self.payer} 一人承担）")

        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 主算法
# --------------------------------------------------------------------------- #
def split_bill(
    items: Iterable[LineItem],
    discount_cents: int = 0,
    delivery_fee_cents: int = 0,
    packaging_fee_cents: int = 0,
    discount_policy: str = "proportional",
    fee_policy: str = "equal",
    payer: str | None = None,
    discount_owner: str | None = None,
    note: str = "",
) -> SplitResult:
    """执行拼单分账。

    :param items: 商品行列表（含下单人）。
    :param discount_cents: 券/活动带来的优惠总额（正数，单位：分）。
    :param delivery_fee_cents: 配送费（单位：分）。
    :param packaging_fee_cents: 打包费（单位：分）。
    :param discount_policy: ``proportional`` / ``equal`` / ``owner``。
    :param fee_policy: ``equal`` / ``proportional``。
    :param payer: 实际垫付人（用于生成转账方案）；缺省取第一位点单人。
    :param discount_owner: 当 ``discount_policy='owner'`` 时，独享优惠的人。
    """
    if discount_policy not in DISCOUNT_POLICIES:
        raise ValueError(f"discount_policy 必须是 {DISCOUNT_POLICIES} 之一，收到 {discount_policy!r}")
    if fee_policy not in FEE_POLICIES:
        raise ValueError(f"fee_policy 必须是 {FEE_POLICIES} 之一，收到 {fee_policy!r}")

    items = [it for it in items if it.qty > 0]
    if not items:
        raise ValueError("报单为空：至少需要一条商品行")

    # 按点单人分组，保持首次出现顺序（便于输出稳定）
    order: list[str] = []
    grouped: dict[str, list[LineItem]] = {}
    for it in items:
        if it.person not in grouped:
            grouped[it.person] = []
            order.append(it.person)
        grouped[it.person].append(it)

    n = len(order)
    subtotals = [sum(x.subtotal_cents for x in grouped[p]) for p in order]
    subtotal_total = sum(subtotals)

    if not payer:
        payer = order[0]

    # ---- 优惠分摊 ------------------------------------------------------ #
    discount_cents = max(0, int(discount_cents))
    if discount_policy == "equal":
        d_weights = [1.0] * n
        d_caps: list[int | None] = [None] * n
    elif discount_policy == "owner":
        if discount_owner and discount_owner in grouped:
            d_weights = [1.0 if p == discount_owner else 0.0 for p in order]
            d_caps = [None] * n
        else:
            # 未指定或指定的人不在报单里 → 退回按比例，并提示
            discount_policy = "proportional"
            d_weights = [float(s) for s in subtotals]
            d_caps = list(subtotals)
            note = (note + " " if note else "") + "未指定优惠归属人，已退回按比例分摊。"
    else:
        d_weights = [float(s) for s in subtotals]
        d_caps = list(subtotals)

    discounts, overflow = allocate_capped(discount_cents, d_weights, d_caps)

    # ---- 费用分摊 ------------------------------------------------------ #
    fees_total = max(0, int(delivery_fee_cents)) + max(0, int(packaging_fee_cents))
    if fee_policy == "proportional" and subtotal_total > 0:
        f_weights = [float(s) for s in subtotals]
    else:
        f_weights = [1.0] * n
    fees = allocate(fees_total, f_weights)

    # ---- 汇总 ---------------------------------------------------------- #
    shares = [
        PersonShare(
            person=order[i],
            subtotal_cents=subtotals[i],
            discount_cents=discounts[i],
            fee_cents=fees[i],
            payable_cents=subtotals[i] - discounts[i] + fees[i],
            items=[f"{x.name}×{x.qty}" for x in grouped[order[i]]],
        )
        for i in range(n)
    ]

    result = SplitResult(
        shares=shares,
        subtotal_cents=subtotal_total,
        discount_cents=discount_cents - overflow,
        delivery_fee_cents=max(0, int(delivery_fee_cents)),
        packaging_fee_cents=max(0, int(packaging_fee_cents)),
        payable_cents=subtotal_total - (discount_cents - overflow) + fees_total,
        discount_policy=discount_policy,
        fee_policy=fee_policy,
        payer=payer,
        overflow_cents=overflow,
        note=note.strip(),
    )
    result.verify()
    return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="split_bill",
        description="麦麦拼单官 · 分账引擎（最大余额法，精确到分）",
    )
    p.add_argument("--input", "-i", help="输入 JSON 文件路径；缺省从 stdin 读取")
    p.add_argument("--markdown", "-m", action="store_true", help="输出可直接贴群的 Markdown 分账单")
    p.add_argument("--policy", choices=DISCOUNT_POLICIES, help="覆盖输入中的优惠分摊口径")
    p.add_argument("--fee-policy", choices=FEE_POLICIES, help="覆盖输入中的费用分摊口径")
    p.add_argument("--payer", help="覆盖垫付人")
    p.add_argument("--discount-owner", help="owner 口径下独享优惠的人")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        payload = json.load(sys.stdin)

    items = [LineItem.from_dict(d) for d in payload.get("items", [])]
    result = split_bill(
        items=items,
        discount_cents=yuan_to_cents(payload.get("discount", 0)),
        delivery_fee_cents=yuan_to_cents(payload.get("delivery_fee", 0)),
        packaging_fee_cents=yuan_to_cents(payload.get("packaging_fee", 0)),
        discount_policy=args.policy or payload.get("discount_policy", "proportional"),
        fee_policy=args.fee_policy or payload.get("fee_policy", "equal"),
        payer=args.payer or payload.get("payer"),
        discount_owner=args.discount_owner or payload.get("discount_owner"),
        note=payload.get("note", ""),
    )

    if args.markdown:
        print(result.to_markdown())
    else:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
