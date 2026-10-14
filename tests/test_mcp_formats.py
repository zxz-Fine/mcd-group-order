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
from plan_coupons import _norm_name, match_coupon_products, plan  # noqa: E402
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

    # ---- 挂券后的真实响应（2026-10-14 实测） ---- #

    PRICE_WITH_COUPON = {
        "success": True,
        "code": 200,
        "data": {
            "productOriginalPrice": 7150,
            "productPrice": 5090,
            "originalPrice": 7150,
            "discount": 2060,
            "price": 5090,
            "productList": [
                {
                    "productCode": "507411",
                    "productName": "麦咖啡™冰美式中杯",
                    "quantity": 1,
                    "originalSubtotal": 1900,
                    "subtotal": 1190,
                },
                {
                    "productCode": "1100",
                    "productName": "巨无霸",
                    "quantity": 1,
                    "originalSubtotal": 2550,
                    "subtotal": 2550,
                },
                {
                    "productCode": "9900012387",
                    "productName": "中薯条买一送一",
                    "quantity": 1,
                    "originalSubtotal": 2700,
                    "subtotal": 1350,
                },
            ],
        },
    }

    def test_coupon_alias_reconnects_claimant(self):
        """券商品(9900012387)经别名归到购物车商品(4810)后，归属人自动接上。"""
        orders = [
            {"person": "阿May", "productCode": "4810", "qty": 1},
            {"person": "阿May", "productCode": "1100", "qty": 1},
            {"person": "老王", "productCode": "507411", "qty": 1},
        ]
        bill = convert(self.PRICE_WITH_COUPON, orders, code_aliases={"9900012387": "4810"})
        self.assertTrue(bill.get("_verified"), bill.get("_warnings"))
        self.assertFalse([i for i in bill["items"] if i["person"] == "__未分配__"])
        self.assertTrue(any("券商品" in w for w in bill.get("_warnings", [])))
        # 「中薯条买一送一」原价 2700（2 份）全归阿May
        tiao = next(i for i in bill["items"] if i["productCode"] == "4810")
        self.assertEqual(tiao["subtotal_cents"], 2700)

    def test_without_alias_coupon_product_is_flagged(self):
        """不传别名时，券商品必须被明确告警为无归属，而不是静默按原名混入。"""
        bill = convert(
            self.PRICE_WITH_COUPON,
            [{"person": "阿May", "productCode": "4810", "qty": 1}],
        )
        self.assertTrue(any("归属" in w for w in bill.get("_warnings", [])))

    def test_coupon_price_response_stays_self_consistent(self):
        """挂券后 originalPrice − discount == price，分账自检必须通过。"""
        bill = convert(
            self.PRICE_WITH_COUPON,
            [{"person": "阿May", "productCode": "9900012387", "qty": 1}],
        )
        self.assertTrue(bill.get("_verified"), bill.get("_warnings"))
        self.assertEqual(sum(i["subtotal_cents"] for i in bill["items"]), 7150)


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


class TestCouponProductCodeMismatch(unittest.TestCase):
    """回归测试：券的 productCode 与菜单的 productCode 对不上。

    2026-10-14 真实联调发现——「中薯条买一送一」的券编码是 ``9900012387``
    （活动虚拟商品），而菜单里中薯条是 ``4810``。早期只做 productCode 精确匹配，
    导致这张券被误判为「本单没有该券适用的商品」。本类锁定修复后的行为。
    """

    def setUp(self):
        self.coupons = load("query_store_coupons_real.json")
        # 购物车里有中薯条(4810)、冰美式(507411)、巨无霸(1100)，没有王牌小食拼
        self.cart = [
            {"productCode": "4810", "name": "中薯条", "unit_price_cents": 1350, "qty": 1},
            {"productCode": "507411", "name": "麦咖啡™冰美式中杯", "unit_price_cents": 1900, "qty": 1},
            {"productCode": "1100", "name": "巨无霸", "unit_price_cents": 2550, "qty": 1},
        ]

    # ---- 名称归一化 ---- #

    def test_strip_marketing_suffix(self):
        self.assertEqual(_norm_name("中薯条买一送一"), "中薯条")
        self.assertEqual(_norm_name("可乐第二杯半价"), "可乐")

    def test_strip_price_prefix_and_symbols(self):
        self.assertEqual(_norm_name("9.9元中杯冰美式"), "中杯冰美式")
        self.assertEqual(_norm_name("麦咖啡™冰美式中杯"), "麦咖啡冰美式中杯")

    # ---- 三级匹配 ---- #

    def test_name_match_recovers_mismatched_code(self):
        """券编码 9900012387 靠商品名回落到购物车的 4810。"""
        coupon = next(
            c for c in plan(self.coupons, self.cart)["usable"] if c["name"] == "中薯条买一送一"
        )
        m = coupon["matched_cart_items"][0]
        self.assertEqual(m["productCode"], "4810")           # 购物车真实编码（下单用）
        self.assertEqual(m["couponProductCode"], "9900012387")  # 券里的虚拟编码（试算可能用）
        self.assertEqual(m["match"], "name_exact")
        self.assertEqual(coupon["confidence"], 0.9)

    def test_exact_code_still_wins(self):
        """冰美式的券编码与菜单一致，必须走 exact_code 而非名称推断。"""
        coupon = next(
            c for c in plan(self.coupons, self.cart)["usable"] if c["name"] == "9.9元中杯冰美式"
        )
        self.assertEqual(coupon["matched_cart_items"][0]["match"], "exact_code")
        self.assertEqual(coupon["confidence"], 1.0)

    def test_genuinely_absent_product_stays_unusable(self):
        """「王牌小食拼」购物车里真没有，不得被名称规则误判为可用。"""
        r = plan(self.coupons, self.cart)
        self.assertIn("王牌小食拼", {u["name"] for u in r["unusable"]})
        self.assertNotIn("王牌小食拼", {u["name"] for u in r["usable"]})

    def test_inferred_match_is_warned(self):
        r = plan(self.coupons, self.cart)
        self.assertTrue(any("推断匹配" in w for w in r["warnings"]))

    def test_apply_uses_coupon_product_code(self):
        """算价必须传**券自己的**编码，否则 600012「促销规则不支持该商品」。"""
        r = plan(self.coupons, self.cart)
        c = next(x for x in r["candidates"] if x["label"] == "中薯条买一送一")
        self.assertEqual(c["apply"][0]["productCode"], "9900012387")      # 算价用
        self.assertEqual(c["apply"][0]["cartProductCode"], "4810")        # 分账归属用
        self.assertEqual(c["apply"][0]["couponCode"], "MCD601CJ080F091PW7090")

    def test_exact_code_coupon_keeps_same_code(self):
        """券编码 == 菜单编码时（冰美式），两个字段一致，不产生替换。"""
        r = plan(self.coupons, self.cart)
        c = next(x for x in r["candidates"] if x["label"] == "9.9元中杯冰美式")
        self.assertEqual(c["apply"][0]["productCode"], "507411")
        self.assertEqual(c["apply"][0]["cartProductCode"], "507411")

    def test_no_stacking_candidates(self):
        """实测不支持多张券（code=600022），不得生成任何叠加候选。"""
        r = plan(self.coupons, self.cart)
        self.assertFalse([x for x in r["candidates"] if " + " in x["label"]])
        # 4 张券 → 最多 1 个基线 + 3 个单券候选
        self.assertLessEqual(len(r["candidates"]), 4)

    def test_max_stack_above_one_is_forced_down(self):
        r = plan(self.coupons, self.cart, max_stack=2)
        self.assertTrue(any("600022" in w for w in r["warnings"]))
        self.assertFalse([x for x in r["candidates"] if " + " in x["label"]])

    def test_replacement_warning_for_virtual_coupon_code(self):
        r = plan(self.coupons, self.cart)
        self.assertTrue(any("活动虚拟商品编码" in w for w in r["warnings"]))

    # ---- 防误匹配 ---- #

    def test_single_char_name_does_not_match(self):
        """单字名称（如「堡」）不得命中任何商品，避免误伤。"""
        coupon = {
            "title": "堡特惠",
            "couponId": "X",
            "products": [{"productCode": "0000", "productName": "堡"}],
        }
        self.assertEqual(match_coupon_products_from(coupon, self.cart), [])

    def test_no_products_is_reported_not_silently_dropped(self):
        r = plan([{"title": "神秘券", "couponId": "Y"}], self.cart)
        self.assertEqual(len(r["usable"]), 0)
        self.assertIn("请人工确认", r["unusable"][0]["reason"])


def match_coupon_products_from(coupon_raw: dict, cart: list) -> list:
    """测试辅助：把裸 dict 券归一化后交给 match_coupon_products。"""
    from plan_coupons import normalize_coupons

    coupons, _ = normalize_coupons([coupon_raw])
    return match_coupon_products(coupons[0], cart)


if __name__ == "__main__":
    unittest.main(verbosity=2)
