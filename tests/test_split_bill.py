#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · 分账引擎单元测试

核心不变量（任何输入都必须成立）：
    sum(每人应付) == 订单应付总额 == 商品小计 - 优惠 + 配送费 + 打包费

运行：
    python -m unittest discover -s tests -v
或：
    python tests/test_split_bill.py
"""

from __future__ import annotations

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "mcd-group-order", "scripts"))

from split_bill import (  # noqa: E402
    LineItem,
    allocate,
    allocate_capped,
    cents_to_yuan,
    split_bill,
    yuan_to_cents,
)


class TestAllocate(unittest.TestCase):
    """最大余额法的数学性质。"""

    def test_sum_invariant_three_way(self):
        """10.00 元 3 人均分：必须是 333/333/334 分，合计恰好 1000。"""
        shares = allocate(1000, [1, 1, 1])
        self.assertEqual(sum(shares), 1000)
        self.assertEqual(sorted(shares), [333, 333, 334])

    def test_sum_invariant_random(self):
        """随机模糊测试：1000 组随机输入，合计必须恒等。"""
        rnd = random.Random(20261014)
        for _ in range(1000):
            n = rnd.randint(1, 12)
            total = rnd.randint(0, 500_000)
            weights = [rnd.randint(0, 1000) for _ in range(n)]
            shares = allocate(total, weights)
            self.assertEqual(len(shares), n)
            self.assertEqual(sum(shares), total, f"n={n} total={total} weights={weights}")

    def test_zero_weight_falls_back_to_equal(self):
        shares = allocate(100, [0, 0, 0])
        self.assertEqual(sum(shares), 100)
        self.assertEqual(sorted(shares), [33, 33, 34])

    def test_empty(self):
        self.assertEqual(allocate(100, []), [])
        self.assertEqual(allocate(0, [1, 2]), [0, 0])

    def test_deterministic(self):
        """同样输入必须给同样输出（不依赖 set/dict 迭代顺序）。"""
        a = allocate(1000, [3, 1, 1])
        b = allocate(1000, [3, 1, 1])
        self.assertEqual(a, b)


class TestAllocateCapped(unittest.TestCase):
    def test_respects_caps(self):
        # 待分摊 1000，权重 [10, 1]，上限 [100, 1000]
        shares, overflow = allocate_capped(1000, [10, 1], [100, 1000])
        self.assertEqual(overflow, 0)
        self.assertEqual(sum(shares), 1000)
        self.assertLessEqual(shares[0], 100)

    def test_overflow_when_all_capped(self):
        shares, overflow = allocate_capped(1000, [1, 1], [100, 100])
        self.assertEqual(sum(shares) + overflow, 1000)
        self.assertEqual(overflow, 800)


class TestSplitBill(unittest.TestCase):
    def setUp(self):
        # 张三 32.00 x1，李四 15.00 x2，王五 9.50 x1  → 小计 71.50
        self.items = [
            LineItem("张三", "麦辣鸡腿堡套餐", yuan_to_cents(32), 1),
            LineItem("李四", "麦香鱼", yuan_to_cents(15), 2),
            LineItem("王五", "中薯条", yuan_to_cents(9.5), 1),
        ]

    def test_total_conservation_proportional(self):
        r = split_bill(self.items, discount_cents=yuan_to_cents(12.5),
                       delivery_fee_cents=yuan_to_cents(9))
        self.assertEqual(r.subtotal_cents, yuan_to_cents(71.5))
        self.assertEqual(r.payable_cents, yuan_to_cents(71.5 - 12.5 + 9))
        self.assertEqual(sum(x.payable_cents for x in r.shares), r.payable_cents)
        r.verify()

    def test_total_conservation_all_policies(self):
        for d_policy in ("proportional", "equal", "owner"):
            for f_policy in ("equal", "proportional"):
                with self.subTest(d=d_policy, f=f_policy):
                    r = split_bill(
                        self.items,
                        discount_cents=yuan_to_cents(12.5),
                        delivery_fee_cents=yuan_to_cents(9),
                        packaging_fee_cents=yuan_to_cents(1),
                        discount_policy=d_policy,
                        fee_policy=f_policy,
                        discount_owner="李四",
                    )
                    r.verify()
                    self.assertEqual(sum(x.payable_cents for x in r.shares), r.payable_cents)

    def test_proportional_is_fair(self):
        """按比例分摊：买得多的人，分摊的优惠也多。"""
        r = split_bill(self.items, discount_cents=yuan_to_cents(10))
        by = {x.person: x for x in r.shares}
        self.assertGreater(by["李四"].discount_cents, by["王五"].discount_cents)
        self.assertGreater(by["张三"].discount_cents, by["王五"].discount_cents)
        self.assertEqual(sum(x.discount_cents for x in r.shares), yuan_to_cents(10))

    def test_owner_policy(self):
        r = split_bill(self.items, discount_cents=yuan_to_cents(10),
                       discount_policy="owner", discount_owner="王五")
        by = {x.person: x for x in r.shares}
        self.assertEqual(by["王五"].discount_cents, yuan_to_cents(10))
        self.assertEqual(by["张三"].discount_cents, 0)
        self.assertEqual(by["李四"].discount_cents, 0)
        r.verify()

    def test_owner_missing_falls_back(self):
        r = split_bill(self.items, discount_cents=yuan_to_cents(10),
                       discount_policy="owner", discount_owner="不存在的人")
        self.assertEqual(r.discount_policy, "proportional")
        self.assertIn("退回按比例", r.note)
        r.verify()

    def test_discount_never_exceeds_subtotal(self):
        """优惠大于商品总额时，不得出现负的应付金额。"""
        r = split_bill(self.items, discount_cents=yuan_to_cents(1000))
        self.assertTrue(all(x.payable_cents >= 0 for x in r.shares))
        self.assertGreater(r.overflow_cents, 0)
        r.verify()

    def test_single_person(self):
        r = split_bill([LineItem("张三", "巨无霸", yuan_to_cents(25), 1)],
                       discount_cents=yuan_to_cents(5),
                       delivery_fee_cents=yuan_to_cents(9))
        self.assertEqual(len(r.shares), 1)
        self.assertEqual(r.shares[0].payable_cents, yuan_to_cents(29))
        self.assertEqual(r.transfers(), [])
        r.verify()

    def test_equal_split_of_odd_cent(self):
        """经典除不尽场景：3 人均摊 10.00 元的配送费。"""
        items = [LineItem(p, "巨无霸", yuan_to_cents(10), 1) for p in ("A", "B", "C")]
        r = split_bill(items, delivery_fee_cents=yuan_to_cents(10), fee_policy="equal")
        fees = sorted(x.fee_cents for x in r.shares)
        self.assertEqual(fees, [333, 333, 334])
        r.verify()

    def test_payer_transfers(self):
        r = split_bill(self.items, discount_cents=yuan_to_cents(10), payer="张三")
        transfers = r.transfers()
        self.assertEqual({t["to"] for t in transfers}, {"张三"})
        self.assertEqual(len(transfers), 2)
        total = sum(yuan_to_cents(t["amount"]) for t in transfers)
        self.assertEqual(total, r.payable_cents - r.shares[0].payable_cents)

    def test_verify_detects_inconsistency(self):
        r = split_bill(self.items)
        r.payable_cents += 1  # 人为破坏
        with self.assertRaises(AssertionError):
            r.verify()

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            split_bill([])

    def test_bad_policy_raises(self):
        with self.assertRaises(ValueError):
            split_bill(self.items, discount_policy="nonsense")

    def test_fuzz_conservation(self):
        """随机模糊测试：随机人数/商品/优惠/费用，总额必须守恒。"""
        rnd = random.Random(1024)
        for _ in range(500):
            n_people = rnd.randint(1, 8)
            items = []
            for p in range(n_people):
                for _ in range(rnd.randint(1, 3)):
                    items.append(
                        LineItem(
                            f"P{p}",
                            "商品",
                            rnd.randint(50, 5000),
                            rnd.randint(1, 3),
                        )
                    )
            subtotal = sum(x.subtotal_cents for x in items)
            discount = rnd.randint(0, subtotal)
            delivery = rnd.choice([0, 600, 900, 1200])
            packaging = rnd.choice([0, 100, 200])
            policy = rnd.choice(["proportional", "equal", "owner"])
            owner = rnd.choice([x.person for x in items])
            r = split_bill(
                items, discount_cents=discount, delivery_fee_cents=delivery,
                packaging_fee_cents=packaging, discount_policy=policy,
                discount_owner=owner,
            )
            r.verify()
            self.assertEqual(sum(x.payable_cents for x in r.shares), r.payable_cents)
            self.assertEqual(r.payable_cents, subtotal - discount + delivery + packaging)

    def test_yuan_cents_roundtrip(self):
        for v in (0, 0.01, 9.9, 12.34, 100, 1024.99):
            self.assertEqual(cents_to_yuan(yuan_to_cents(v)), f"{v:.2f}")


class TestMarkdown(unittest.TestCase):
    def test_markdown_contains_total(self):
        r = split_bill(
            [LineItem("张三", "巨无霸", yuan_to_cents(25), 2)],
            discount_cents=yuan_to_cents(10),
            delivery_fee_cents=yuan_to_cents(9),
            payer="张三",
        )
        md = r.to_markdown()
        self.assertIn("拼单分账单", md)
        self.assertIn("¥49.00", md)  # 50 - 10 + 9
        self.assertIn("分账口径", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
