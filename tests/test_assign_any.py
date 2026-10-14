#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · 「随便」报单指派测试

覆盖解析层（意图识别）与指派层（汉堡指派、不重样、忌口、边界）。

运行：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "mcd-group-order", "scripts")
sys.path.insert(0, SCRIPTS)

from assign_any import assign, _avoided_by  # noqa: E402
from match_menu import hamburger_candidates, match_orders, normalize_menu  # noqa: E402
from parse_orders import parse_orders  # noqa: E402

# 贴近真实门店的菜单（含辣味汉堡，用于验证忌口）
MENU = {
    "data": {
        "categories": [
            {
                "name": "汉堡\n牛鱼肉堡",
                "meals": [
                    {"code": "1100", "tags": ["单品"]},
                    {"code": "1200", "tags": ["单品"]},
                    {"code": "1300", "tags": ["单品"]},
                    {"code": "1120", "tags": ["单品"]},
                    {"code": "1600", "tags": ["单品"]},
                ],
            },
            {"name": "小食甜品\n/其他", "meals": [{"code": "4810", "tags": ["小食"]}]},
        ],
        "meals": {
            "1100": {"name": "巨无霸", "currentPrice": "25.5"},
            "1200": {"name": "麦辣鸡腿堡", "currentPrice": "21"},
            "1300": {"name": "板烧鸡腿堡", "currentPrice": "21.5"},
            "1120": {"name": "双层吉士汉堡", "currentPrice": "23"},
            "1600": {"name": "麦香鱼", "currentPrice": "22.5"},
            "4810": {"name": "薯条", "currentPrice": "13.5"},
            "9900016073": {"name": "龙焰鸡腿堡三件套", "currentPrice": "26.9"},
        },
    }
}


def run(text: str) -> dict:
    """跑完整链路：parse → match → assign。"""
    parsed = parse_orders(text)
    matched = match_orders(parsed["orders"], MENU)
    return assign(matched, MENU)


class TestIntentDetection(unittest.TestCase):
    """解析层：识别「随便」类泛称。"""

    def test_plain_any_words(self):
        for word in ("随便", "都行", "都可以", "随意", "无所谓", "你看着办", "不挑", "都OK"):
            with self.subTest(word=word):
                p = parse_orders(f"@张三 {word}")
                self.assertEqual(p["any_count"], 1, f"{word} 应被识别为泛称")
                self.assertEqual(p["orders"][0]["items"][0]["intent"], "any")

    def test_trailing_particles_and_lead_verb(self):
        for word in ("随便吧", "随便了", "都行呗", "随便来", "来个随便", "随便点呀"):
            with self.subTest(word=word):
                p = parse_orders(f"@张三 {word}")
                self.assertEqual(p["any_count"], 1, f"{word} 应被识别为泛称")

    def test_explicit_order_is_not_any(self):
        """「随便来个巨无霸」已指定餐品，不能算泛称。"""
        for word in ("随便来个巨无霸", "随便一个巨无霸", "巨无霸", "随便来份薯条"):
            with self.subTest(word=word):
                p = parse_orders(f"@张三 {word}")
                self.assertEqual(p["any_count"], 0, f"{word} 不应被识别为泛称")

    def test_consecutive_any_merged_into_one(self):
        """「都行，你看着办」是一次「随便」，不是两次。"""
        p = parse_orders("@小李 都行，你看着办")
        items = p["orders"][0]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["intent"], "any")

    def test_merged_qty_takes_max(self):
        """「随便x2，都行」→ 数量 2。"""
        p = parse_orders("@小李 随便x2，都行")
        items = p["orders"][0]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["qty"], 2)

    def test_note_survives_detection(self):
        p = parse_orders("@小孙 随便，不要辣")
        it = p["orders"][0]["items"][0]
        self.assertEqual(it["intent"], "any")
        self.assertIn("辣", it["note"])

    def test_any_warning_is_emitted(self):
        p = parse_orders("@张三 随便\n@李四 都行")
        self.assertTrue(any("随便" in w for w in p["warnings"]))

    def test_normal_items_have_no_intent_field(self):
        p = parse_orders("@张三 巨无霸")
        self.assertNotIn("intent", p["orders"][0]["items"][0])


class TestBurgerDetection(unittest.TestCase):
    """菜单层：汉堡候选识别。"""

    def test_category_name_catches_juwuba(self):
        """「巨无霸」名字里没有「堡」，靠分类名「汉堡 牛鱼肉堡」认出来。"""
        names = [m["name"] for m in hamburger_candidates(normalize_menu(MENU))]
        self.assertIn("巨无霸", names)

    def test_combo_is_excluded(self):
        """套餐不算「一个汉堡」。"""
        names = [m["name"] for m in hamburger_candidates(normalize_menu(MENU))]
        self.assertNotIn("龙焰鸡腿堡三件套", names)

    def test_sides_are_excluded(self):
        names = [m["name"] for m in hamburger_candidates(normalize_menu(MENU))]
        self.assertNotIn("薯条", names)

    def test_candidates_keep_menu_order(self):
        """候选顺序必须确定（便于复现指派结果）。"""
        codes = [m["code"] for m in hamburger_candidates(normalize_menu(MENU))]
        self.assertEqual(codes, ["1100", "1200", "1300", "1120", "1600"])


class TestAssign(unittest.TestCase):
    """指派层：核心行为。"""

    def test_single_person_gets_a_burger(self):
        r = run("@张三 随便")
        self.assertEqual(r["assigned_count"], 1)
        self.assertEqual(r["items"][0]["matched_name"], "巨无霸")
        self.assertTrue(r["items"][0]["assigned"])

    def test_multiple_people_get_different_burgers(self):
        """核心需求：多人「随便」应指派不同口味。"""
        r = run("@A 随便\n@B 都行\n@C 你看着办")
        names = [x["name"] for x in r["assignments"]]
        self.assertEqual(len(names), 3)
        self.assertEqual(len(set(names)), 3, f"应指派 3 种不同汉堡，实际 {names}")

    def test_avoids_explicitly_ordered_burger(self):
        """李四点过巨无霸，其他人「随便」应避开它。"""
        r = run("李四：巨无霸\n@A 随便\n@B 随便")
        assigned = {x["name"] for x in r["assignments"]}
        self.assertNotIn("巨无霸", assigned)
        self.assertEqual([x["name"] for x in r["excluded"]], ["巨无霸"])

    def test_qty_two_gets_two_different_burgers(self):
        r = run("@小赵 随便 x2")
        names = [x["name"] for x in r["assignments"]]
        self.assertEqual(len(names), 2)
        self.assertNotEqual(names[0], names[1])

    def test_avoid_spicy_when_noted(self):
        """「随便，不要辣」不得指派麦辣鸡腿堡。"""
        r = run("@小孙 随便，不要辣\n@A 随便\n@B 随便")
        sun = next(x for x in r["assignments"] if x["person"] == "小孙")
        self.assertNotIn("辣", sun["name"])

    def test_avoid_beef_when_noted(self):
        r = run("@A 随便，不要牛")
        self.assertNotIn("巨无霸", r["assignments"][0]["name"])
        self.assertNotIn("吉士", r["assignments"][0]["name"])

    def test_impossible_avoidance_is_warned(self):
        """忌口把所有候选排光时必须告警，而不是静默返回。"""
        menu = {
            "data": {
                "meals": {"1100": {"name": "巨无霸", "currentPrice": "25.5"}},
                "categories": [{"name": "牛鱼肉堡", "meals": [{"code": "1100"}]}],
            }
        }
        parsed = parse_orders("@A 随便，不要牛")
        matched = match_orders(parsed["orders"], menu)
        r = assign(matched, menu)
        self.assertEqual(r["assigned_count"], 1)
        self.assertTrue(any("忌口" in w for w in r["warnings"]))

    def test_exhausted_pool_warns_about_repeats(self):
        r = run("@A 随便\n@B 随便\n@C 随便\n@D 随便\n@E 随便\n@F 随便")
        self.assertTrue(any("重复口味" in w for w in r["warnings"]))

    def test_no_burger_in_menu_warns(self):
        menu = {"data": {"meals": {"4810": {"name": "薯条", "currentPrice": "13.5"}}}}
        parsed = parse_orders("@A 随便")
        matched = match_orders(parsed["orders"], menu)
        r = assign(matched, menu)
        self.assertEqual(r["assigned_count"], 0)
        self.assertTrue(any("没有识别到" in w for w in r["warnings"]))

    def test_no_any_items_passes_through(self):
        r = run("@张三 巨无霸\n@李四 麦香鱼")
        self.assertEqual(r["assigned_count"], 0)
        self.assertEqual([i["matched_name"] for i in r["items"]], ["巨无霸", "麦香鱼"])

    def test_deterministic(self):
        """同样输入必须给同样输出。"""
        text = "@A 随便\n@B 随便\n@C 随便"
        first = [x["name"] for x in run(text)["assignments"]]
        for _ in range(5):
            self.assertEqual([x["name"] for x in run(text)["assignments"]], first)

    def test_assignments_carry_person_and_price(self):
        r = run("@周七 随便")
        a = r["assignments"][0]
        self.assertEqual(a["person"], "周七")
        self.assertEqual(a["from"], "随便")
        self.assertTrue(a["code"])
        self.assertNotEqual(a["price"], "0.00")

    def test_assigned_items_have_codes_for_downstream(self):
        """指派结果必须带 matched_code，否则无法进入算价/分账。"""
        r = run("@A 随便\n@B 随便")
        for i in r["items"]:
            self.assertTrue(i["matched_code"])
            self.assertTrue(i["matched_name"])

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            assign({"items": []}, MENU, kind="drink")


class TestConfirmMessage(unittest.TestCase):
    """「列出来问」：确认话术必须逐条列出谁拿到了什么。"""

    def test_lists_every_assignee(self):
        r = run("@小孙 随便，不要辣\n@周七 随便\n@小李 都行")
        msg = r["confirm_message"]
        for a in r["assignments"]:
            self.assertIn(a["person"], msg)
            self.assertIn(a["name"], msg)
        self.assertIn("3 种口味，无重复", msg)

    def test_includes_note(self):
        r = run("@小孙 随便，不要辣")
        self.assertIn("不要辣", r["confirm_message"])

    def test_mentions_excluded_burgers(self):
        r = run("李四：巨无霸\n@A 随便")
        self.assertIn("巨无霸", r["confirm_message"])
        self.assertIn("未参与指派", r["confirm_message"])

    def test_warns_on_repeats(self):
        r = run("@A 随便\n@B 随便\n@C 随便\n@D 随便\n@E 随便\n@F 随便")
        self.assertIn("口味有重复", r["confirm_message"])

    def test_empty_when_nothing_assigned(self):
        self.assertEqual(run("@张三 巨无霸")["confirm_message"], "")

    def test_empty_when_no_burger_candidates(self):
        menu = {"data": {"meals": {"4810": {"name": "薯条", "currentPrice": "13.5"}}}}
        parsed = parse_orders("@A 随便")
        r = assign(match_orders(parsed["orders"], menu), menu)
        self.assertEqual(r["confirm_message"], "")


class TestAvoidRules(unittest.TestCase):
    def test_rule_parsing(self):
        self.assertEqual(_avoided_by("不要辣"), ("辣",))
        self.assertEqual(_avoided_by(""), ())
        self.assertIn("巨无霸", _avoided_by("不吃牛肉"))
        self.assertIn("麦香鱼", _avoided_by("不要鱼"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
