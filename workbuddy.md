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

## 会话 5 · MCP 真实联调（关键修正）

**用户**：mcp已添加

**WorkBuddy**：读取 `~/.workbuddy-ai/mcp.json` 确认 `mcd-mcp` 已配置，随后对麦当劳 MCP 发起**真实只读调用**做字段校准
（`now-time-info`、`query-nearby-stores`、`query-meals`、`query-store-coupons`、`query-my-coupons`、
`available-coupons`、`calculate-price`、`list-nutrition-foods`、`campaign-calendar`）。

联调暴露出 **5 处与原始假设不符**，其中 3 处会让脚本直接跑不通：

1. **金额单位是「分」**：`calculate-price` 的 `discount`/`price`/`deliveryPrice`/`packingPrice`
   全部为分。原设计按「元」处理，会导致金额放大 100 倍。
2. **`query-meals` 的 `data.meals` 是以餐品编码为 key 的字典，不是数组**：
   原 `normalize_menu()` 按数组遍历，实际会得到空菜单。
3. **优惠券是按商品绑定的，没有面额也没有门槛**：原「满 X 减 Y」的券模型完全不成立，
   按门槛剪枝的逻辑失效。
4. **返回格式不统一**：`query-my-coupons` / `available-coupons` / `campaign-calendar`
   返回 Markdown 文本而非 JSON。
5. **`query-meals` / `calculate-price` 的 `storeCode`/`orderType`/`beType` 均为必填**，
   且到店自取（beType=1）不传 `beCode`、得来速（beType=5）必须传。

**据此完成的修正**：

- `split_bill.py`：新增 `*_cents` 字段与 `subtotal_cents` 精确小计支持，全程以分为单位
- `match_menu.py`：重写 `normalize_menu()`，兼容「编码为 key 的字典」与嵌套 `data.meals`
- `plan_coupons.py`：**重写为按 `productCode` 的券↔商品映射模型**，不再预测优惠金额
- `from_price.py`（新增）：把 `calculate-price` 响应 + 报单归属转成分账输入，
  用最大余额法把商品小计精确分摊到人，并内置金额自检
- `SKILL.md`：按实测参数重写工作流，新增「MCP 实测要点」章节
- `MCP_INTEGRATION.md`：新增「实测校准结果」章节
- 新增 `tests/test_mcp_formats.py`（15 项）与 `tests/fixtures/`，用实测响应锁定字段结构

**验证**：测试从 22 项增至 **37 项，全部通过**。端到端实测（storeCode=1950564）：
`calculate-price` 返回原价 ¥74.00 → 4 人分账 25.50/25.50/13.50/9.50，合计精确等于 ¥74.00。

**未验证部分（如实记录）**：该账号 `query-store-coupons` 与 `query-my-coupons` 均返回空，
**券链路未能端到端验证**，仅按官方 schema 锁定字段结构；`create-order` 等写操作亦未实测。

---

## 使用说明

本项目在 WorkBuddy 中运行的最小闭环：

1. 在 WorkBuddy「连接器」中按 `mcp-config.example.json` 配置 `mcd-mcp` 并启用。
2. 将 `skills/mcd-group-order/` 复制到 `~/.workbuddy-ai/skills/`。
3. 在对话中粘贴群聊报单文本，即可触发「麦麦拼单官」。
