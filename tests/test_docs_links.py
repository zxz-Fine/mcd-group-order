#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦拼单官 · 站点链接完整性测试

要防的是什么
------------
GitHub Pages 只发布 ``docs/`` 目录，``docs/`` 就是站点的**根**。
于是任何形如 ``../xxx`` 的相对链接都会**逃出站点根**，在线上变成 404 ——
而本地用 ``file://`` 或起个静态服务在仓库根目录打开时，它却是好的。

这个 bug 真实发生过：``docs/index.html`` 的「动画演示」写的是
``../demo/animation.html``，本地看着没问题，线上 404；
而且 ``demo/animation.html`` 内部还嵌着
``<iframe src="../examples/sample_card.html">`` —— 第三层雷也埋着。

所以这个测试检查三件事（对 ``docs/`` 下所有 HTML）：

1. 相对链接解析后**不得逃出 ``docs/``**（越界即失败 —— 就是上面那个 bug）；
2. 相对链接指向的文件**必须存在**；
3. 页内锚点 ``#xxx`` 必须在同一文件里有对应的 ``id="xxx"``。

绝对 URL（http/https/mailto/data 等）一律跳过 —— 那是外部资源，不归本站管。

运行：python -m unittest tests.test_docs_links -v
"""

from __future__ import annotations

import os
import unittest
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")

# 这些前缀表示「不是本站内部相对路径」，直接跳过
_EXTERNAL_PREFIXES = (
    "http://", "https://", "//", "mailto:", "tel:", "data:", "javascript:", "#",
)

_LINK_ATTRS = {"href", "src", "poster"}


class _LinkCollector(HTMLParser):
    """收集文档里的链接与锚点 id。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if not value:
                continue
            if key in _LINK_ATTRS:
                self.links.append(value)
            elif key == "id":
                self.ids.add(value)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def _html_files() -> list[str]:
    out: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(DOCS):
        for name in filenames:
            if name.lower().endswith((".html", ".htm")):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def _is_external(link: str) -> bool:
    low = link.strip().lower()
    return not low or low.startswith(_EXTERNAL_PREFIXES)


class TestDocsLinks(unittest.TestCase):
    """docs/ 下所有 HTML 的内部链接必须落在站点内且真实存在。"""

    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        if not os.path.isdir(DOCS):
            raise unittest.SkipTest("docs/ 不存在")
        cls.files = _html_files()
        cls.parsed: dict[str, _LinkCollector] = {}
        for path in cls.files:
            with open(path, "r", encoding="utf-8") as fh:
                parser = _LinkCollector()
                parser.feed(fh.read())
            cls.parsed[path] = parser

    def test_docs_dir_not_empty(self):
        """docs/ 下至少要有一个 HTML，否则下面的检查等于没跑。"""
        self.assertTrue(self.files, "docs/ 下没有任何 HTML 文件")

    def test_no_relative_link_escapes_docs_root(self):
        """相对链接不得逃出 docs/ —— 越界在 Pages 上必然是 404。"""
        offenders: list[str] = []
        for path, parser in self.parsed.items():
            base = os.path.dirname(path)
            for link in parser.links:
                if _is_external(link):
                    continue
                target = os.path.normpath(os.path.join(base, link.split("#")[0].split("?")[0]))
                if not _within(target, DOCS):
                    offenders.append(
                        f"{os.path.relpath(path, ROOT)} → {link!r}"
                        f"（解析为 {os.path.relpath(target, ROOT)}，已超出 docs/）"
                    )
        self.assertEqual(
            offenders, [],
            "发现逃出 docs/ 的相对链接 —— 线上会 404：\n  " + "\n  ".join(offenders),
        )

    def test_relative_targets_exist(self):
        """相对链接指向的文件（或目录下的 index.html）必须真实存在。"""
        missing: list[str] = []
        for path, parser in self.parsed.items():
            base = os.path.dirname(path)
            for link in parser.links:
                if _is_external(link):
                    continue
                target = os.path.normpath(os.path.join(base, link.split("#")[0].split("?")[0]))
                if not _within(target, DOCS):
                    continue  # 越界由上一个测试负责报错
                if os.path.isdir(target):
                    target = os.path.join(target, "index.html")
                if not os.path.exists(target):
                    missing.append(
                        f"{os.path.relpath(path, ROOT)} → {link!r}"
                        f"（找不到 {os.path.relpath(target, ROOT)}）"
                    )
        self.assertEqual(
            missing, [],
            "发现指向不存在文件的相对链接：\n  " + "\n  ".join(missing),
        )

    def test_anchors_resolve(self):
        """页内锚点 #xxx 必须在同一文件里有 id="xxx"。"""
        bad: list[str] = []
        for path, parser in self.parsed.items():
            for link in parser.links:
                if not link.startswith("#") or len(link) == 1:
                    continue
                anchor = link[1:]
                if anchor not in parser.ids:
                    bad.append(f"{os.path.relpath(path, ROOT)} → {link!r}（没有 id={anchor!r}）")
        self.assertEqual(bad, [], "发现悬空锚点：\n  " + "\n  ".join(bad))

    def test_animation_iframe_target_present(self):
        """回归：动画页嵌入的分账卡片必须在站点内可达。

        这条是上面那次 404 的直接回归用例 —— 卡片原本在仓库根的
        examples/ 下，Pages 根本不会发布它。
        """
        anim = os.path.join(DOCS, "demo", "animation.html")
        if not os.path.exists(anim):
            self.skipTest("docs/demo/animation.html 不存在")
        parser = self.parsed[anim]
        iframes = [l for l in parser.links if "sample_card" in l]
        self.assertTrue(iframes, "动画页里没找到卡片 iframe")
        for link in iframes:
            target = os.path.normpath(os.path.join(os.path.dirname(anim), link))
            self.assertTrue(_within(target, DOCS), f"卡片 iframe 逃出 docs/：{link!r}")
            self.assertTrue(os.path.exists(target), f"卡片 iframe 指向不存在的文件：{link!r}")


def _within(path: str, root: str) -> bool:
    """path 是否位于 root 之内（含 root 本身）。"""
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except ValueError:  # 不同盘符
        return False


if __name__ == "__main__":
    unittest.main(verbosity=2)
