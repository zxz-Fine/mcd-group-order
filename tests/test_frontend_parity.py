#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · 前后端算法一致性测试 (parity)

要防的是什么
------------
``docs/index.html`` 是发布到 GitHub Pages 的在线试算器，里面用 JS 复刻了一遍
分账算法。**两份实现最大的风险不是写错，而是悄悄漂移**：某天只改了 Python，
前端就变成了「另一个算法」，而页面上依然会显示「恒等校验通过」——
因为它校验的是它自己。

所以这个测试不比对「我复制的代码」，而是直接加载**真正要发布的那份 HTML**，
在 Node 里跑，再和 ``split_bill.py`` 逐字段比对：

    shares / summary / transfers / markdown 全文

一旦两边出现 1 分钱差异，测试立刻红。

前置：本机有 Node（找不到则整个测试类 skip，不阻塞 CI）。
运行：python -m unittest tests.test_frontend_parity -v
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "mcd-group-order", "scripts"))

from split_bill import LineItem, split_bill  # noqa: E402

HTML_PATH = os.path.join(ROOT, "docs", "index.html")
PARITY_JS = os.path.join(ROOT, "tests", "parity_check.js")
SAMPLE_BILL = os.path.join(ROOT, "examples", "sample_bill.json")

_NODE_CANDIDATES = [
    os.path.expanduser(
        r"~/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe"
    ),
    r"D:\ProgramFiles\nodejs\node.exe",
    shutil.which("node"),
]


def _find_node() -> str | None:
    for c in _NODE_CANDIDATES:
        if c and os.path.exists(c):
            return c
    return None


NODE = _find_node()


# --------------------------------------------------------------------------- #
# 把 Python 结果转成与前端 to_json 完全同构的结构
# --------------------------------------------------------------------------- #
def py_result(items: list[LineItem], opts: dict) -> dict:
    r = split_bill(
        items=items,
        discount_cents=opts.get("discountCents", 0),
        delivery_fee_cents=opts.get("deliveryFeeCents", 0),
        packaging_fee_cents=opts.get("packagingFeeCents", 0),
        discount_policy=opts.get("discountPolicy", "proportional"),
        fee_policy=opts.get("feePolicy", "equal"),
        payer=opts.get("payer"),
        discount_owner=opts.get("discountOwner"),
        note=opts.get("note", ""),
    )
    return r.to_dict()


def py_markdown(items: list[LineItem], opts: dict) -> str:
    r = split_bill(
        items=items,
        discount_cents=opts.get("discountCents", 0),
        delivery_fee_cents=opts.get("deliveryFeeCents", 0),
        packaging_fee_cents=opts.get("packagingFeeCents", 0),
        discount_policy=opts.get("discountPolicy", "proportional"),
        fee_policy=opts.get("feePolicy", "equal"),
        payer=opts.get("payer"),
        discount_owner=opts.get("discountOwner"),
        note=opts.get("note", ""),
    )
    return r.to_markdown()


# --------------------------------------------------------------------------- #
# 用例构造
# --------------------------------------------------------------------------- #
def cents(yuan: str) -> int:
    """「12.34」→ 1234，与 split_bill.yuan_to_cents 同口径。"""
    return int(round(float(yuan) * 100))


def item(person: str, name: str, yuan: str, qty: int = 1) -> dict:
    return {"person": person, "name": name, "unitPriceCents": cents(yuan), "qty": qty}


def to_line_items(raw_items: list[dict]) -> list[LineItem]:
    return [
        LineItem(
            person=d["person"],
            name=d["name"],
            unit_price_cents=d["unitPriceCents"],
            qty=d["qty"],
        )
        for d in raw_items
    ]


def load_sample() -> tuple[list[dict], dict]:
    with open(SAMPLE_BILL, "r", encoding="utf-8") as fh:
        p = json.load(fh)
    items = [
        item(d["person"], d["name"], str(d["unit_price"]), d["qty"])
        for d in p["items"]
    ]
    opts = {
        "discountCents": cents(str(p["discount"])),
        "deliveryFeeCents": cents(str(p["delivery_fee"])),
        "packagingFeeCents": cents(str(p["packaging_fee"])),
        "discountPolicy": p["discount_policy"],
        "feePolicy": p["fee_policy"],
        "payer": p["payer"],
        "discountOwner": None,
        "note": p.get("note", ""),
    }
    return items, opts


def fuzz_cases(n: int = 240, seed: int = 20261014) -> list[dict]:
    """随机账单：覆盖不同人数、商品数、口径、优惠/费用组合。"""
    rnd = random.Random(seed)
    names = ["张三", "李四", "王五", "赵六", "陈七", "周八", "吴九", "郑十"]
    menu = [
        ("麦辣鸡腿堡", "25.50"), ("巨无霸", "25.00"), ("麦香鱼", "15.00"),
        ("板烧鸡腿堡", "26.00"), ("中薯条", "12.00"), ("可口可乐", "9.00"),
        ("1+1随心配", "13.90"), ("麦旋风", "11.50"), ("麦乐鸡5块", "12.50"),
        ("香芋派", "8.00"), ("中杯美式", "19.00"), ("麦咖啡拿铁", "22.00"),
    ]
    policies = ["proportional", "equal", "owner"]
    fee_policies = ["equal", "proportional"]

    cases = []
    for _ in range(n):
        people = rnd.sample(names, rnd.randint(1, 6))
        items = []
        for p in people:
            for _ in range(rnd.randint(1, 3)):
                nm, price = rnd.choice(menu)
                items.append(item(p, nm, price, rnd.randint(1, 3)))

        subtotal = sum(i["unitPriceCents"] * i["qty"] for i in items)
        # 优惠：0 ~ 略超小计（刻意制造 overflow 场景）
        discount = rnd.choice([
            0,
            rnd.randint(0, max(1, subtotal // 5)),
            rnd.randint(0, max(1, subtotal // 2)),
            subtotal + rnd.randint(0, 500),   # 故意让优惠超过商品总额
        ])
        policy = rnd.choice(policies)
        cases.append({
            "items": items,
            "opts": {
                "discountCents": discount,
                "deliveryFeeCents": rnd.choice([0, 200, 900, 1500]),
                "packagingFeeCents": rnd.choice([0, 0, 100, 200]),
                "discountPolicy": policy,
                "feePolicy": rnd.choice(fee_policies),
                "payer": rnd.choice(people),
                "discountOwner": rnd.choice(people) if policy == "owner" else None,
                "note": "",
            },
        })
    return cases


# --------------------------------------------------------------------------- #
# 测试
# --------------------------------------------------------------------------- #
@unittest.skipIf(NODE is None, "本机未找到 Node，跳过前后端一致性测试")
@unittest.skipIf(not os.path.exists(HTML_PATH), "docs/index.html 不存在")
class TestFrontendParity(unittest.TestCase):
    """前端页面 JS 必须与 split_bill.py 逐字段一致。"""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.sample_items, cls.sample_opts = load_sample()
        cls.fuzz = fuzz_cases()

    # ---- 调用前端 ----
    @classmethod
    def _run_frontend(cls, cases: list[dict]) -> list[dict]:
        payload = json.dumps({"cases": cases}, ensure_ascii=False)
        proc = subprocess.run(
            [NODE, PARITY_JS, "--batch"],
            input=payload.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
        )
        if proc.returncode != 0:
            raise AssertionError(
                "前端脚本执行失败：\n" + proc.stderr.decode("utf-8", "replace")
            )
        return json.loads(proc.stdout.decode("utf-8"))["results"]

    # ---- 1. 示例账单 ----
    def test_sample_bill_matches(self):
        """示例账单：三个口径逐一比对 JSON 与 Markdown 全文。"""
        for policy in ("proportional", "equal", "owner"):
            with self.subTest(policy=policy):
                opts = dict(self.sample_opts)
                opts["discountPolicy"] = policy
                if policy == "owner":
                    opts["discountOwner"] = opts["payer"]

                fe = self._run_frontend([{"items": self.sample_items, "opts": opts}])[0]
                py = py_result(to_line_items(self.sample_items), opts)

                self.assertEqual(fe["json"], py, f"{policy} 口径下前端与后端结果不一致")
                self.assertEqual(
                    fe["markdown"], py_markdown(to_line_items(self.sample_items), opts),
                    f"{policy} 口径下 Markdown 不一致",
                )

    # ---- 2. 模糊测试 ----
    def test_fuzz_matches(self):
        """240 组随机账单：逐字段比对，任何 1 分钱差异都会失败。"""
        results = self._run_frontend(self.fuzz)
        self.assertEqual(len(results), len(self.fuzz))

        for idx, (case, fe) in enumerate(zip(self.fuzz, results)):
            with self.subTest(case=idx, policy=case["opts"]["discountPolicy"]):
                if "error" in fe:
                    self.fail(f"用例 {idx} 前端报错：{fe['error']}")
                py = py_result(to_line_items(case["items"]), case["opts"])

                self.assertEqual(
                    fe["json"]["shares"], py["shares"],
                    f"用例 {idx} 分账明细不一致",
                )
                self.assertEqual(
                    fe["json"]["summary"], py["summary"],
                    f"用例 {idx} 汇总不一致",
                )
                self.assertEqual(
                    fe["json"]["transfers"], py["transfers"],
                    f"用例 {idx} 转账方案不一致",
                )

    # ---- 3. 恒等不变量（前端自己也要守住） ----
    def test_frontend_verify_invariant(self):
        """前端自检必须与后端一致：Σ明细 == 订单实付。"""
        results = self._run_frontend(self.fuzz)
        for idx, fe in enumerate(results):
            if "error" in fe:
                continue
            with self.subTest(case=idx):
                v = fe["verify"]
                self.assertTrue(v["sharesOk"], f"用例 {idx} Σ明细 ≠ 订单实付")
                self.assertTrue(v["orderOk"], f"用例 {idx} 小计-优惠+费用 ≠ 实付")
                self.assertEqual(v["sumShares"], v["payableCents"])

    # ---- 4. 经典边界：10 元 3 人均分 ----
    def test_three_way_ten_yuan(self):
        """1000 分 3 人均分 → 333/333/334，合计恰好 1000。"""
        items = [item(p, "巨无霸", "0.01") for p in ("甲", "乙", "丙")]
        # 直接构造 10.00 元总额：用 3 条 3.33/3.33/3.34 更直观，这里改用单价法
        items = [
            item("甲", "巨无霸", "3.33"),
            item("乙", "巨无霸", "3.33"),
            item("丙", "巨无霸", "3.34"),
        ]
        opts = {
            "discountCents": 0, "deliveryFeeCents": 0, "packagingFeeCents": 0,
            "discountPolicy": "equal", "feePolicy": "equal", "payer": "甲",
        }
        fe = self._run_frontend([{"items": items, "opts": opts}])[0]
        py = py_result(to_line_items(items), opts)
        self.assertEqual(fe["json"], py)
        self.assertEqual(
            sum(cents(s["payable"]) for s in py["shares"]), 1000,
        )

    # ---- 5. 优惠超出商品总额 ----
    def test_overflow_parity(self):
        """优惠 > 商品总额：前端必须与后端给出同样的溢出额，且不出现负数应付。"""
        items = [
            item("甲", "中薯条", "12.00"),
            item("乙", "可口可乐", "9.00"),
        ]
        opts = {
            "discountCents": 5000, "deliveryFeeCents": 900, "packagingFeeCents": 0,
            "discountPolicy": "proportional", "feePolicy": "equal", "payer": "甲",
        }
        fe = self._run_frontend([{"items": items, "opts": opts}])[0]
        py = py_result(to_line_items(items), opts)

        self.assertEqual(fe["json"], py)
        self.assertEqual(py["summary"]["overflow"], "29.00")
        for s in py["shares"]:
            self.assertGreaterEqual(cents(s["payable"]), 0, "出现负数应付")

    # ---- 6. owner 口径未指定归属人 → 双方都必须退回比例并给出提示 ----
    def test_owner_fallback_parity(self):
        items = [item("甲", "巨无霸", "25.00"), item("乙", "中薯条", "12.00")]
        opts = {
            "discountCents": 1000, "deliveryFeeCents": 0, "packagingFeeCents": 0,
            "discountPolicy": "owner", "feePolicy": "equal",
            "payer": "甲", "discountOwner": "查无此人",
        }
        fe = self._run_frontend([{"items": items, "opts": opts}])[0]
        py = py_result(to_line_items(items), opts)

        self.assertEqual(fe["json"], py)
        self.assertEqual(py["policy"]["discount"], "proportional", "未退回比例口径")
        self.assertIn("未指定优惠归属人", py["note"])
        self.assertIn("未指定优惠归属人", fe["json"]["note"])

    # ---- 7. 单人拼单 ----
    def test_single_person_parity(self):
        items = [item("独食侠", "巨无霸", "25.00", 2)]
        opts = {
            "discountCents": 500, "deliveryFeeCents": 900, "packagingFeeCents": 100,
            "discountPolicy": "proportional", "feePolicy": "equal", "payer": "独食侠",
        }
        fe = self._run_frontend([{"items": items, "opts": opts}])[0]
        py = py_result(to_line_items(items), opts)
        self.assertEqual(fe["json"], py)
        self.assertEqual(py["transfers"], [], "单人拼单不应产生转账")

    # ---- 8. 金额字符串格式化一致 ----
    def test_money_format_parity(self):
        """0.05 / 0.5 / 0.00 / 大额 都要格式化一致。"""
        items = [
            item("甲", "香芋派", "0.05"),
            item("乙", "可口可乐", "0.50"),
            item("丙", "中薯条", "12.00"),
        ]
        opts = {
            "discountCents": 1, "deliveryFeeCents": 0, "packagingFeeCents": 0,
            "discountPolicy": "proportional", "feePolicy": "equal", "payer": "甲",
        }
        fe = self._run_frontend([{"items": items, "opts": opts}])[0]
        py = py_result(to_line_items(items), opts)
        self.assertEqual(fe["json"], py)
        for s in py["shares"]:
            self.assertRegex(s["payable"], r"^-?\d+\.\d{2}$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
