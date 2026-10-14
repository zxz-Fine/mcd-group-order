#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · MCP 真实数据格式回归测试

这些测试用**实测**的麦当劳 MCP 响应做输入，锁定真实字段结构，
防止未来重构时又把 `data.meals` 当成数组、把「分」当成「元」。

运行：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "mcd-group-order", "scripts")
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, SCRIPTS)

from from_price import convert  # noqa: E402
from match_menu import match_orders, normalize_menu  # noqa: E402
from plan_coupons import plan  # noqa: E402
from split_bill import cents_to_yuan, split_bill, LineItem  # noqa: E402


def load(name: str) -> dict:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


class TestMenuNormalization(unittest.TestCase):
    """锁定 query-meals 的真实结构：data.meals 是以编码为 key 的字典。"""

    def test_normalize_real_mcp_response(self):
        menu = normalize_menu(load("query_meals.json"))
        codes = {m["code"] for m in menu}
        self.assertIn("1100", codes)
        self.assertIn("4810", codes)
        self.assertEqual(len(menu), 6)

    def test_price_from_currentPrice(self):
        menu = {m["code"]: m for m in normalize_menu(load("query_meals.json"))}
        # 巨无霸 25.5 元 → 2550 分
        self.assertEqual(menu["1100"]["price_cents"], 2550)
        self.assertEqual(menu["1100"]["name"], "巨无霸")
        # 薯条 13.5 元 → 1350 分
        self.assertEqual(menu["4810"]["price_cents"], 1350)

    def test_normalize_still_accepts_plain_list(self):
        menu = normalize_menu([{"code": "X1", "name": "测试商品", "price": 9.9}])
        self.assertEqual(len(menu), 1)
        self.assertEqual(menu[0]["price_cents"], 990)

    def test_normalize_accepts_code_keyed_dict(self):
        menu = normalize_menu({"A1": {"name": "甲", "currentPrice": "5"}})
        self.assertEqual(menu[0]["code"], "A1")
        self.assertEqual(menu[0]["price_cents"], 500)

    def test_match_against_real_menu(self):
        menu = load("query_meals.json")
        orders = [
            {"person": "张三", "items": [{"name": "巨无霸", "qty": 1, "note": ""}]},
            {"person": "李四", "items": [{"name": "薯条", "qty": 1, "note": ""}]},
            {"person": "王五", "items": [{"name": "可乐", "qty": 2, "note": ""}]},
            {"person": "赵六", "items": [{"name": "龙焰鸡腿堡三件套", "qty": 1, "note": ""}]},
        ]
        result = match_orders(orders, menu)
        self.assertEqual(result["unmatched_count"], 0)
        by_q = {i["query"]: i for i in result["items"]}
        self.assertEqual(by_q["巨无霸"]["matched_code"], "1100")
        self.assertEqual(by_q["薯条"]["matched_code"], "4810")
        self.assertEqual(by_q["可乐"]["matched_code"], "9900008751")
        self.assertEqual(by_q["龙焰鸡腿堡三件套"]["matched_code"], "9900016073")


class TestFromPrice(unittest.TestCase):
    """锁定 calculate-price 的真实结构：金额单位是分。"""

    def test_conversion_is_exact(self):
        orders = [
            {"person": "张三", "productCode": "1100", "qty": 1},
            {"person": "李四", "productCode": "1100", "qty": 1},
            {"person": "王五", "productCode": "4810", "qty": 1},
            {"person": "赵六", "productCode": "9900008751", "qty": 1},
        ]
        bill = convert(load("calculate_price.json"), orders)
        self.assertTrue(bill.get("_verified"), bill.get("_warnings"))
        self.assertEqual(sum(i["subtotal_cents"] for i in bill["items"]), 7400)
        # 2 个巨无霸共 5100 分，两个人各 2550
        zhang = next(i for i in bill["items"] if i["person"] == "张三")
        self.assertEqual(zhang["subtotal_cents"], 2550)

    def test_end_to_end_split_matches_price(self):
        """端到端：算价响应 → 分账输入 → 分账结果，总额必须等于算价接口的 price。"""
        orders = [
            {"person": "张三", "productCode": "1100", "qty": 1},
            {"person": "李四", "productCode": "1100", "qty": 1},
            {"person": "王五", "productCode": "4810", "qty": 1},
            {"person": "赵六", "productCode": "9900008751", "qty": 1},
        ]
        bill = convert(load("calculate_price.json"), orders, payer="张三")
        items = [LineItem.from_dict(d) for d in bill["items"]]
        r = split_bill(
            items,
            discount_cents=bill["discount_cents"],
            delivery_fee_cents=bill["delivery_fee_cents"],
            packaging_fee_cents=bill["packaging_fee_cents"],
            payer=bill["payer"],
        )
        r.verify()
        self.assertEqual(r.payable_cents, 7400)
        self.assertEqual(sum(x.payable_cents for x in r.shares), 7400)
        # 4 个人，张三垫付 → 3 笔转账
        self.assertEqual(len(r.transfers()), 3)

    def test_indivisible_subtotal_stays_exact(self):
        """除不尽场景：3 个商品共 100.00 元，3 人各 1 个。"""
        price = {
            "data": {
                "originalPrice": 10000,
                "discount": 0,
                "price": 10000,
                "productList": [
                    {
                        "productCode": "X",
                        "productName": "测试品",
                        "quantity": 3,
                        "originalSubtotal": 10000,
                        "subtotal": 10000,
                    }
                ],
            }
        }
        orders = [
            {"person": "A", "productCode": "X", "qty": 1},
            {"person": "B", "productCode": "X", "qty": 1},
            {"person": "C", "productCode": "X", "qty": 1},
        ]
        bill = convert(price, orders)
        parts = sorted(i["subtotal_cents"] for i in bill["items"])
        self.assertEqual(sum(parts), 10000)
        self.assertEqual(parts, [3333, 3333, 3334])

    def test_inconsistency_is_reported(self):
        """算价结果与明细对不上时必须报警，而不是静默输出。"""
        price = load("calculate_price.json")
        price["data"]["price"] = 9999  # 人为破坏
        bill = convert(
            price,
            [{"person": "张三", "productCode": "1100", "qty": 2}],
        )
        self.assertFalse(bill.get("_verified"))
        self.assertTrue(any("不自洽" in w for w in bill.get("_warnings", [])))

    def test_missing_claimant_is_reported(self):
        bill = convert(
            load("calculate_price.json"),
            [{"person": "张三", "productCode": "1100", "qty": 2}],
        )
        self.assertTrue(any("归属" in w for w in bill.get("_warnings", [])))


class TestCouponPlanning(unittest.TestCase):
    """锁定券的「按商品绑定」模型：没有面额、没有门槛。"""

    def setUp(self):
        self.coupons = load("query_store_coupons.json")["data"]
        self.cart = [
            {"productCode": "1100", "name": "巨无霸", "unit_price_cents": 2550, "qty": 2},
            {"productCode": "4810", "name": "中薯条", "unit_price_cents": 1350, "qty": 1},
            {"productCode": "9900008751", "name": "可乐", "unit_price_cents": 950, "qty": 1},
        ]

    def test_usable_and_unusable_split(self):
        r = plan(self.coupons, self.cart)
        usable_names = {u["name"] for u in r["usable"]}
        unusable_names = {u["name"] for u in r["unusable"]}
        self.assertIn("中薯条买一送一", usable_names)
        self.assertIn("可乐第二杯半价", usable_names)
        # 购物车里没有麦旋风 → 该券不可用，且要给出原因
        self.assertIn("麦旋风买一送一", unusable_names)
        reason = next(u for u in r["unusable"] if u["name"] == "麦旋风买一送一")
        self.assertEqual(reason["reason"], "本单没有该券适用的商品")

    def test_candidates_include_baseline_and_singles(self):
        r = plan(self.coupons, self.cart)
        labels = [c["label"] for c in r["candidates"]]
        self.assertTrue(any("基线" in x for x in labels))
        self.assertIn("中薯条买一送一", labels)
        self.assertIn("可乐第二杯半价", labels)

    def test_apply_payload_has_coupon_ids(self):
        r = plan(self.coupons, self.cart)
        single = next(c for c in r["candidates"] if c["label"] == "中薯条买一送一")
        self.assertEqual(single["apply"][0]["productCode"], "4810")
        self.assertEqual(single["apply"][0]["couponId"], "CP20261014001")
        self.assertEqual(single["apply"][0]["couponCode"], "CC-9F3A21")

    def test_no_saving_prediction(self):
        """本脚本刻意不预测优惠金额——真实券数据里没有面额。"""
        r = plan(self.coupons, self.cart)
        for c in r["candidates"]:
            self.assertNotIn("theoretical_saving", c)

    def test_accepts_raw_mcp_envelope(self):
        r = plan(load("query_store_coupons.json"), self.cart)
        self.assertEqual(r["coupon_total"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
