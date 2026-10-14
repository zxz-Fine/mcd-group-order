# WorkBuddy 开发对话上下文

> 本文件用于核验本项目在开发过程中真实使用了腾讯 WorkBuddy 智能体，以符合「麦当劳程序员创意开发大赛」WorkBuddy 专项奖励的联动条件。
> 以下为开发过程的关键对话上下文（脱敏整理）。

---

## 会话 1 · 赛事调研与选题

**用户**：https://github.com/M-China/mcd-developer-innovation-challenge 最近麦当劳出了一个比赛，看一下这个比赛是干嘛的？

**WorkBuddy**：抓取仓库 README 与活动规则后，输出结论——这是麦当劳程序员创意开发大赛，面向中国大陆开发者，基于麦当劳 MCP 开发 Skill，**排名唯一依据是 GitHub 公开 Star 数**；报名与排名 2026-10-09 10:30 ~ 10-25 23:59；仓库需包含 `README.md`、`CONTEST_DECLARATION.md`、`MCP_INTEGRATION.md`、`mcp-config.example.json`、`workbuddy.md`；通过 Issue 报名。

---

## 会话 2 · 创意构思

**用户**：一起构思一个能打的 Skill 创意

**WorkBuddy**：先抓取 `M-China/mcd-mcp-server` 的完整 Tool 清单（33 个 Tool），再抓取活动仓库的 `RANKING.md` 榜单做红海/空白分析，得到：

- 红海赛道：优惠券/省钱策略（榜首 278★）、热量营养搭配（149★）、派对主题活动预约（234★）、玄学人格测试（210★）。
- 空白赛道：**拼单/团餐分账（几乎无人）**、开发者行为联动（仅 10★ 雏形）、积分理财（最高 14★）。

据此提出 4 个候选方向，并推荐「麦麦拼单官」，理由：赛道空白、传播面最广、用到了几乎无人使用的企业团餐能力（`query-meal-assistance`）。

**用户**：选「麦麦拼单官」，单人开发。

**WorkBuddy**：据此把范围收敛为单人可交付的单点闭环——**报单 → 最优券组合 → 一键下单 → AA 分账图**，并把「优惠怎么分摊才不吵架」确立为核心技术卖点。

---

## 会话 3 · 仓库搭建

**用户**：开工

**WorkBuddy** 完成：

1. 抓取官方 `CONTEST_DECLARATION.md` 原文，逐字落库（该文件内容不可改动）。
2. 生成合规文件：`README.md`、`MCP_INTEGRATION.md`、`mcp-config.example.json`、`workbuddy.md`、`LICENSE`、`.gitignore`。
3. 实现核心脚本：
   - `scripts/split_bill.py`：**最大余额法（Largest Remainder Method）** 精确分摊引擎，保证 Σ(每人应付) 恒等于订单应付总额，永不出现 0.01 尾差。
   - `scripts/parse_orders.py`：群聊报单解析器，支持 `@昵称`/`昵称：`/`昵称 ` 三种前缀、`x2`/`×2`/`2份` 三种数量写法、备注提取。
   - `scripts/plan_coupons.py`：券组合候选生成（门槛过滤 + 互斥剪枝 + beam search），输出 ≤8 个候选供 `calculate-price` 试算。
   - `scripts/render_card.py`：生成自包含的分账卡片 HTML，可直接截图发群。
4. 编写 `skills/mcd-group-order/SKILL.md`，编排上述脚本与麦当劳 MCP 的 18 个 Tool。
5. 编写单元测试并验证总额守恒、零尾差、边界场景。

---

## 会话 4 · 自测与修正

**WorkBuddy**：运行 `tests/test_split_bill.py`，覆盖以下场景并全部通过：

- 总额守恒：任意随机输入下 Σ(每人应付) == 应付总额
- 零尾差：`0.01` 级别的最小金额与 `3 人均摊 10.00 元` 等经典除不尽场景
- 边界：单人参单、单人单品、优惠大于商品总额（按比例分摊后不为负）
- 分账口径切换：按比例 / 均摊 / 谁用券谁受益，三种口径下总额均守恒

---

## 使用说明

本项目在 WorkBuddy 中运行的最小闭环：

1. 在 WorkBuddy「连接器」中按 `mcp-config.example.json` 配置 `mcd-mcp` 并启用。
2. 将 `skills/mcd-group-order/` 复制到 `~/.workbuddy-ai/skills/`。
3. 在对话中粘贴群聊报单文本，即可触发「麦麦拼单官」。
