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

**当时未验证部分**：该账号 `query-store-coupons` 与 `query-my-coupons` 均返回空，
**券链路未能端到端验证**，仅按官方 schema 锁定字段结构；`create-order` 等写操作亦未实测。

---

## 会话 6 · 真实券联调，修复券编码不一致缺陷

**背景**：用户重新领取优惠券后，再次调用券相关工具验证能否查到。

**实测结果**：

| 工具 | 返回 |
|---|---|
| `query-my-coupons` | 卡包 **11 张**可用券（Markdown 文本） |
| `available-coupons` | 状态由「可领取」全部变为「已领取」 |
| `query-store-coupons` | 当前门店（1950564）**3 张**可用券，**真实 JSON** |

真实返回证实会话 5 中按官方 schema 推断的字段结构**完全正确**
（`title` / `couponId` / `couponCode` / `tradeDateTime` / `products[].productCode`）。

**新发现的缺陷（最关键）**：

「中薯条买一送一」的 `products[].productCode` 是 **`9900012387`**，
而菜单里中薯条的编码是 **`4810`**——两者**对不上**。`99xxxxxxxx` 是「活动虚拟商品编码」，
只在券/促销体系里存在，购物车里不会出现。

`plan_coupons.py` 早期只做 `productCode` 精确匹配，导致购物车里明明有中薯条，
该券却被误判为「本单没有该券适用的商品」。

**修复（第一步 · 匹配侧）**：

- 新增 `match_coupon_products()`，三级降信匹配：
  `exact_code`（1.0）→ `name_exact`（0.9）→ `name_contains`（0.7）
- 新增 `_norm_name()`：匹配前剥离价格数字（`9.9元`）、营销词（`买一送一` / `第二杯半价`）、
  商标符号（`™`）与标点
- 单字名称（如「堡」）不参与包含匹配，防止误伤
- 新增 `tests/fixtures/query_store_coupons_real.json` 与 `TestCouponProductCodeMismatch`

**挂券实测（关键 · 推翻了上一步的挂载假设）**：

修好匹配后，我用真券实际调了 `calculate-price`，结果**推翻了我原本的挂载设想**：

| 测试 | 挂法 | 结果 |
|---|---|---|
| 基线 | 不挂券 | ✅ ¥58.00 → **¥50.90**（冰美式门店自动减 ¥7.10） |
| A | 中薯条券挂 `4810` | ❌ `600012 促销规则不支持该商品` |
| B | 冰美式券挂 `507411` | ✅ ¥58.00 → **¥48.90** |
| C | 中薯条券挂 `9900012387` | ✅ ¥71.50 → **¥50.90** |
| D | 同时挂 2 张券 | ❌ `600022 暂不支持多张券使用` |

三条结论：

1. **算价必须传券自己的 `productCode`**。传菜单里的等价商品编码会失败。
   我原先以为「券编码只是别名，下单要用购物车编码」——**错了**。
2. **券商品 = 券的商品形态**。挂 `9900012387` 返回的商品名是「中薯条买一送一」，
   `originalSubtotal=2700`（2 份）、`subtotal=1350`——即「2 份按 1 份计价」，
   与用户报单的「中薯条 ×1」不是同一件事，下单前必须与用户确认。
3. **不支持券叠加**。因此不存在「券组合优化」，只有「单券择优」。

**修复（第二步 · 按实测纠正）**：

- `plan_coupons.py`：`max_stack` 强制为 1、删除叠加候选生成；
  `apply[]` 改为 `productCode`（**券编码，算价用**）+ `cartProductCode`（购物车编码，分账用）
- `from_price.py`：新增 `code_aliases` 与 `--code-alias 券编码=购物车编码`，
  把券商品归到报单归属人；不传则明确告警「无归属人」
- `SKILL.md`：第 5 条整条重写（挂载规则原先写反了），新增第 6 条（不支持叠加）、
  第 7 条（门店自动促销）；阶段 3 更名「单券择优」，阶段 4 补 `--code-alias`
- `MCP_INTEGRATION.md`：2.4 节重写并补入三组挂载实测对比；错误码表补
  `600012` / `600022` / `600050`

**验证**：测试从 37 项增至 **53 项，全部通过**。真实券响应端到端跑通：
`calculate-price` 原价 ¥71.50、优惠 ¥20.60、应付 **¥50.90** →
`from_price`（含 `--code-alias 9900012387=4810`）→ `split_bill`
→ 阿May **¥37.37** / 老王 **¥13.53**，合计精确 ¥50.90，与算价接口完全一致。

**仍未验证**：`create-order` 等写操作。

---

## 会话 7 · 支持「随便」报单（泛称指派）

**需求**（用户原话）：「应该加一个"随便"，因为有时候大家不想看具体吃什么，会上报一整个
"随便"，可以将这个随便指定为汉堡，不同口味的汉堡」

**实现**：给流水线加一条支路，三层分工——

1. **`parse_orders.py`**：新增泛称意图识别，命中则输出 `intent: "any"`
   - 受控词表（`随便` / `都行` / `你看着办` / `无所谓` / `不挑` …），
     去掉「来个」前缀与「吧/了/呀/来/点/个」语气后缀后**整段**匹配
   - **`随便来个巨无霸` 不算泛称**——它已经指定了餐品
   - 同一行连续泛称会合并：`都行，你看着办` 是**一次**「随便」，不是两次
2. **`match_menu.py`**：`normalize_menu()` 保留 `category` / `tags`；
   新增 `is_burger()` / `hamburger_candidates()`；泛称条目**跳过匹配**直接转交
3. **`assign_any.py`**（新增）：把泛称指派为具体汉堡

**指派规则**：

| 规则 | 实现 |
|---|---|
| 不重样 | 先发「未分配」的候选，发完了再从头轮换 |
| 避开已点 | 从候选池排除别人明确点过的编码 |
| 忌口 | 备注含「不要辣 / 不吃牛 / 不要鱼」→ 排除对应关键词 |
| x2 拆分 | `随便 x2` → 2 个**不同口味**的汉堡 |
| 确定性 | 按菜单顺序轮换，不引入随机数 |

**汉堡识别为什么「分类优先」**：真实菜单里「巨无霸」被放在「巨无霸 牛鱼肉堡」分类下，
**名字里并没有「堡」字**，只靠名称关键词会漏掉它。所以先看 `data.categories[].name`，
名称关键词只作兜底；同时排除套餐（「三件套」等不算「一个汉堡」）。

**踩坑（由测试暴露的真实缺陷）**：

1. 备注词表不含肉类 → `不要牛` 没被识别为备注，变成独立餐品条目，**忌口静默失效**。
   已补 `牛肉|牛|鸡肉|鸡|鱼|猪|虾`（长词在前，避免「不要牛肉」被切成「不要牛」+「肉」）。
2. `随便来` / `随便点呀` 漏识别 → 语气后缀表补 `来|点|一个|个`。

**验证**：测试 53 → **80 项全通过**（新增 `tests/test_assign_any.py` 27 项）。
真实菜单演示：6 个汉堡候选，5 位「随便」→ **5 种不同口味**；
备注「不要辣」的小孙自动跳过麦辣鸡腿堡、拿到板烧鸡腿堡；李四点过的巨无霸被排除。

---

## 会话 8 · 在线分账试算器（GitHub Pages）

**起因**：看到同赛道的参赛作品 `hujjun/mcd-saver-strategist` 有一个在线试算页
（`hujjun.github.io/mcd-saver-strategist/#playground`），用户问「这种网站怎么部署的」。

**侦察结论**（curl 实测，不是猜）：

| 证据 | 结果 |
|---|---|
| 响应头 | `Server: GitHub.com`、`x-github-edge-region: japaneast` |
| 体积 | 43 KB **单个 HTML** |
| 外部依赖 | `<script src>` / `<link href>` 数量 **0** —— 全部内联 |

→ 它把算法跑在**浏览器 JS** 里，所以不需要后端。这是纯静态站点 + GitHub Pages。

**关键判断**：我们的分账引擎是**纯整数确定性运算**，天然适合搬到前端。

### 做了什么

1. **`docs/index.html`**（单文件，0 外部依赖）
   - 把 `split_bill.py` 的 `allocate()` / `allocate_capped()` / `split_bill()` **1:1 复刻**成 JS
   - 交互：按人编辑商品、全局优惠/费用/口径、垫付人
   - 输出：汇总四格、恒等校验徽章、每人应付表、转账方案、**三口径对比**、Markdown/JSON 导出
   - 支持粘贴 JSON 导入（与 CLI 输入格式一致）
2. **`.github/workflows/pages.yml`** —— `configure-pages@v5` 带 `enablement: true`，
   首次运行自动把 Pages 源切到 "GitHub Actions"，**不需要手动去 Settings 点**
3. **`docs/.nojekyll`** —— 防止 Jekyll 处理下划线开头文件
4. **`tests/test_frontend_parity.py`**（8 项）+ `tests/parity_check.js`
5. **`tests/shot.js`** —— 用本机 Chrome（`playwright-core` + `channel:'chrome'`，零下载）
   做可视检查：断言无 console error、页面数据自洽、输出 `assets/preview.png`

### 为什么一致性测试这样写（核心设计）

复刻算法最大的风险**不是写错，而是悄悄漂移**——某天只改了 Python，前端就变成另一个算法，
而页面上依然显示「恒等校验通过」（因为它校验的是它自己）。

所以 `parity_check.js` **不比对「我复制的代码」**，而是：

1. 读出**真正要发布的那份 `docs/index.html`**，用正则抽出其中的 `<script>`；
2. 给 `document` / `navigator` 打桩（Node 18+ 的 `globalThis.navigator` 是只读的，
   必须用 `Object.defineProperty`），在 Node 里**执行真实产物**；
3. 取回 `window.MCDSplit`，跑 240 组随机账单 + 各种边界；
4. 与 `split_bill.py` 逐字段比对 `shares` / `summary` / `transfers` / Markdown 全文。

这样校验的是**实际产物**，而不是「我以为的产物」。

**踩坑**：

- `return` 放在 `loadBill()` 之前才生效——批量分支必须先于单文件分支，否则
  `node parity_check.js --batch` 会去读一个名为 `--batch` 的文件而 ENOENT。
- Git Bash 的 `/tmp` 与原生 Windows Python 的 `/tmp` **不是同一个路径**，
  中间文件必须放项目内或走 stdin，否则会拿到空文件——而 `diff` 两边都空时
  会返回 0，**产生「✅ 完全一致」的假阳性**。这个坑第一次就踩了，已改为 stdin 传输。

**验证**：测试 86 → **94 项全通过**；Chrome 实测页面 0 console 报错，
示例账单结果与 Python 完全一致（¥168.90 / -¥30.00 / +¥9.00 / **¥147.90**，
转账 4 笔）。

---

## 使用说明

本项目在 WorkBuddy 中运行的最小闭环：

1. 在 WorkBuddy「连接器」中按 `mcp-config.example.json` 配置 `mcd-mcp` 并启用。
2. 将 `skills/mcd-group-order/` 复制到 `~/.workbuddy-ai/skills/`。
3. 在对话中粘贴群聊报单文本，即可触发「麦麦拼单官」。
