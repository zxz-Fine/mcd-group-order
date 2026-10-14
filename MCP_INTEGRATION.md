# MCP 集成说明

本文档说明「麦麦拼单官」实际调用的麦当劳 MCP Server、Tool、调用流程与业务价值，供赛事审核核验。

- **MCP Server**：麦当劳中国 MCP 服务（`https://mcp.mcd.cn`）
- **传输协议**：Streamable HTTP
- **鉴权方式**：请求头 `Authorization: Bearer <YOUR_MCP_TOKEN>`
- **接入方式**：WorkBuddy 自定义连接器（配置见 [`mcp-config.example.json`](./mcp-config.example.json)）

---

## 一、调用的 Tool 清单

| # | Tool | 中文名 | 在本项目中的作用 | 调用时机 |
|---|---|---|---|---|
| 1 | `now-time-info` | 获取当前时间信息 | 判断当前时段（早餐/正餐/夜宵），决定菜单可售范围与活动有效期 | 每次会话开始 |
| 2 | `query-nearby-stores` | 查询附近可用门店 | 根据组织者提供的地址定位到店取餐门店 | 到店拼单场景 |
| 3 | `delivery-query-addresses` | 获取用户可配送地址列表 | 外送拼单场景下选择配送地址 | 外送场景 |
| 4 | `delivery-query-stores` | 查询可配送的门店列表 | 外送拼单场景下确定出餐门店 | 外送场景 |
| 5 | `query-meals` | 查询当前可售卖的餐品列表 | **核心**：拉取门店菜单，把群聊里口语化的餐品名（如"麦辣鸡腿堡套餐"）映射到餐品编码 | 每次拼单 |
| 6 | `query-meal-detail` | 查询餐品详情 | 解析套餐组成与可替换选项，处理"不要冰""换可乐"等个性化备注 | 存在套餐/备注时 |
| 7 | `list-nutrition-foods` | 餐品营养信息列表 | 在分账卡片中附加每人热量参考，供拼单成员自主选择 | 需要热量信息时 |
| 8 | `query-store-coupons` | 查询当前门店可用优惠券列表 | **核心**：拿到该门店可用的券，作为券组合优化的候选集 | 每次拼单 |
| 9 | `query-my-coupons` | 我的优惠券查询 | 合并账户内已领券，扩大候选集 | 每次拼单 |
| 10 | `available-coupons` | 麦麦省券列表查询 | 发现尚未领取的可领券，提示组织者先领再用 | 券优化前 |
| 11 | `auto-bind-coupons` | 麦麦省一键领券 | 在用户明确同意后，一键领取可用券再下单，把优惠落到实处 | 用户确认后 |
| 12 | `query-meal-assistance` | 查询助餐服务 | 企业团餐场景下确认门店是否支持助餐服务（大额/多人团餐） | 企业团餐场景 |
| 13 | `calculate-price` | 商品价格计算 | **核心**：对每个候选券组合试算应付总价，选出最省方案；同时取得权威的优惠金额与费用明细用于分账 | 券优化与下单前 |
| 14 | `create-order` | 创建订单 | 在组织者确认方案后正式下单，返回订单详情与支付链接 | 用户确认后 |
| 15 | `query-order` | 查询订单详情 | 下单后跟踪出餐/配送进度，并回填分账卡片的订单号 | 下单后 |
| 16 | `campaign-calendar` | 活动日历查询工具 | 结合当月活动（如 1+1、第二份半价）判断是否存在比券更优的活动组合 | 券优化时 |
| 17 | `order-list` | 查询历史订单 | 学习组织者团队的常点组合，生成"一键复购上次拼单" | 复购场景 |
| 18 | `mall-points-products` / `mall-product-detail` | 麦麦商城商品查询 | 拼单满额时提示可用积分兑换的加餐，提升客单价与体验 | 附加推荐 |

> 说明：本项目**不使用** `draw-lottery`、`mall-create-order` 等涉及直接消耗用户积分/资产的高风险写操作；`create-order`、`auto-bind-coupons` 属于写操作，**必须经用户在对话中明确确认后才执行**。

---

## 二、实测校准结果（2026-10-14 真实联调）

以下结论来自对麦当劳 MCP 的**真实调用**（`storeCode=1950564`，北京建外 SOHO 店），
不是文档推测。这些细节直接决定了集成代码怎么写，也是本项目最重要的工程产出之一。

### 2.1 金额单位是「分」

`calculate-price` 的返回（节选实测）：

```json
{ "data": {
    "originalPrice": 7400, "discount": 0, "price": 7400,
    "productOriginalPrice": 7400, "productPrice": 7400,
    "deliveryPrice": 0, "packingPrice": 0, "tablewarePrice": 0,
    "productList": [
      {"productCode": "1100", "productName": "巨无霸", "quantity": 2,
       "originalSubtotal": 5100, "subtotal": 5100}
    ],
    "takeWayList": [{"code": "eat-in", "title": "堂食"}, {"code": "take-in-store", "title": "外带"}],
    "enjoyed": {"amountType": "3", "realDiscount": 5, "balance": 0},
    "enjoyable": {"amountType": "3", "realDiscount": 5, "balance": 1200}
}}
```

- **所有金额字段单位为分**（整数）。展示时除以 100。
- `data.price` 是应付现价，`data.discount` 是优惠金额，`data.deliveryPrice` / `data.packingPrice` 是费用明细。
- `data.enjoyed.realDiscount` 的单位是**元**（与其它字段不同），`enjoyable.balance`（门槛差额）单位又是**分**——同一响应里单位不统一，务必逐个确认。
- 本项目 `from_price.py` 直接透传分，避免二次舍入。

### 2.2 返回格式不统一

| 工具 | 实际返回格式 |
|---|---|
| `now-time-info`、`query-nearby-stores`、`query-meals`、`calculate-price`、`query-store-coupons` | JSON |
| `query-my-coupons`、`available-coupons`、`campaign-calendar` | **Markdown 文本** |
| `list-nutrition-foods` | 一个包在 JSON 字符串里的 TSV（`[160]{productName,energyKcal,...}:`） |

因此本地脚本只接受**结构化 JSON**，Markdown 由 LLM 先解析归一。

### 2.3 `query-meals` 的菜单是字典而非数组

```json
{ "data": {
    "categories": [{"name": "巨无霸\n牛鱼肉堡", "meals": [{"code": "1100", "tags": ["单品"]}]}],
    "meals": {
      "1100": {"name": "巨无霸", "currentPrice": "25.5", "originalPrice": "25.5", "canWithOrder": false}
    }
}}
```

- `data.meals` 是**以餐品编码为 key 的字典**。
- `data.categories[].meals[]` 只有 `code` 和 `tags`，**没有名称**，取名称必须查 `data.meals`。
- 价格字段为 `currentPrice`（字符串，单位元），划线价是 `originalPrice`。
- 部分商品带 `discountType`（如「随单购麦金卡优惠」）与 `withOrder`，用不上时忽略即可。

`match_menu.py` 的 `normalize_menu()` 已兼容上述结构，直接把整个响应传进去即可。

### 2.4 优惠券是「按商品绑定」，没有面额与门槛

`query-store-coupons` 的单条券（结构样例，字段名来自实测 schema）：

```json
{ "title": "中薯条买一送一", "couponId": "CP20261014001", "couponCode": "CC-9F3A21",
  "tradeDateTime": "2026-10-14 00:00:00 - 2026-10-20 23:59:59",
  "products": [{"productName": "中薯条", "productCode": "4810"}] }
```

**券数据里没有 `discountAmount`、也没有 `threshold`。** 这意味着：

- 「用满减门槛做候选剪枝」的常见思路在这里**不成立**。
- 本项目 `plan_coupons.py` 改为**按 `productCode` 建立券↔商品映射**，
  产出「券 × 商品」候选，交由 `calculate-price` 试算定夺。
- 脚本**刻意不预测优惠金额**，避免向用户给出误导性的「预计省 X 元」。

> 实测中该账号 `query-store-coupons` 与 `query-my-coupons` 均返回空，
> 券链路未能端到端验证，仅按官方 schema 锁定字段结构。

### 2.5 `beCode` 的传递规则（易错）

- `beType=1` 到店自取：`query-nearby-stores` 返回的门店**不含 `beCode`**，后续调用**不传**。
- `beType=5` 得来速：返回的门店**含 `beCode`**，后续调用**必须传**。
- `orderType`：到店（含得来速）为 `1`，外送（含麦乐送、团餐）为 `2`。
- 收藏门店为空时 `searchType=1` 会返回 `code=600050`，需改用 `searchType=2` + `city` + `keyword`。

### 2.6 其它实测细节

- `query-nearby-stores` 返回的门店含 `reservation` 与 `reservationTimeOptions[]`（支持预约时），
  预约场景需要完整展示，缺失会导致用户无法预约。
- `query-meals` 的 `storeCode`、`orderType`、`beType` 三者**均为必填**，缺一会失败。
- `list-nutrition-foods` 是**全量**餐品热量表（约 160 条），与门店菜单无关联，
  需按名称自行匹配，不能直接用于算价。

---

## 三、调用流程

```
┌────────────────────────────────────────────────────────────────────────┐
│ 阶段 0 · 会话初始化                                                    │
│   now-time-info → 判定时段；确认就餐方式（到店/外送/得来速/团餐）      │
└────────────────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────────────────┐
│ 阶段 1 · 门店定位                                                      │
│   到店自取：query-nearby-stores(searchType=1, beType=1)                │
│             └ 若返回 600050（收藏为空）→ searchType=2 + city + keyword │
│   得来速  ：query-nearby-stores(searchType=2, beType=5) → 取 beCode    │
│   外送    ：delivery-query-addresses → delivery-query-stores           │
│   团餐    ：同外送 + query-meal-assistance                             │
└────────────────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────────────────┐
│ 阶段 2 · 报单解析与菜单匹配（本地脚本，不消耗 MCP 配额）               │
│   群聊文本 → parse_orders.py → 结构化报单 JSON                         │
│   query-meals(storeCode, orderType, beType) → data.meals（字典）       │
│   match_menu.py：口语名 → productCode（整个响应直接传入）              │
│   query-meal-detail：处理套餐替换与个性化备注（modification.key 规则） │
└────────────────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────────────────┐
│ 阶段 3 · 券组合优化（核心）                                            │
│   query-store-coupons（JSON）/ query-my-coupons（Markdown）            │
│   / available-coupons（Markdown）→ 归一为 JSON                         │
│   campaign-calendar 补充活动维度                                       │
│        → plan_coupons.py：按 productCode 建立券↔商品映射，剪枝出候选   │
│        → calculate-price 逐个试算（挂 couponId/couponCode 到 item）    │
│        → 取 data.price（分）最低者为最终方案                           │
│   （用户同意时可 auto-bind-coupons 先领券再试算）                      │
└────────────────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────────────────┐
│ 阶段 4 · 分账（核心）                                                  │
│   from_price.py：算价响应 + 报单归属 → 分账输入（金额全程为分）        │
│        └ 按商品把小计用最大余额法精确分摊到人 + 金额自检               │
│   split_bill.py：三种口径精确分摊，Σ(每人应付) ≡ 订单实付              │
│   产出：分账表 + 收款文案 + 分账卡片 HTML                              │
└────────────────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────────────────┐
│ 阶段 5 · 下单与回填                                                    │
│   create-order（需用户确认；取餐方式取自 data.takeWayList[]）          │
│   → query-order 跟踪 → 回填订单号到分账卡片                            │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 四、业务价值

1. **把 MCP 的能力从"单人点餐"扩展到"多人协作"**
   麦当劳 MCP 的所有点餐类 Tool 都是围绕**单个账户**设计的。而真实场景里，办公室、宿舍、朋友聚会点麦当劳，绝大多数是**一群人凑一单**。本项目补上了"多人 → 单人账户"这最后一公里的编排层：报单收集、合并下单、优惠分摊、账目清算。

2. **让优惠券真正被用上**
   `query-store-coupons` / `available-coupons` 提供了丰富的券数据，但普通用户在点餐时很难判断"这 5 张券怎么组合最省"。本项目用候选组合枚举 + `calculate-price` 权威试算，把"凭感觉用券"变成"算过再用券"。

3. **消除拼单最大的社交摩擦**
   拼单吵的从来不是"点了什么"，而是"**优惠怎么分**"。本项目把分账口径（按比例 / 均摊 / 谁用券谁受益）做成显式选项，并用最大余额法保证分摊金额精确到分、总额恒等，从机制上消除争议。

4. **盘活企业团餐场景**
   `query-meal-assistance` 面向企业团餐，目前生态中使用率极低。本项目将其作为团队拼单的默认路径，为 MCP 的企业侧能力提供落地场景。

---

## 五、调用频次与限流

- 麦当劳 MCP 限流为 **600 次/分钟**。
- 本项目将**报单解析、券组合候选生成、分账计算**全部放在本地脚本完成，只有必要的菜单/券/试算/下单动作才调用 MCP，单次拼单的 MCP 调用量通常控制在 **20 次以内**，远低于限流阈值。
- `calculate-price` 的试算次数通过候选剪枝控制在 **≤8 次**。

---

## 六、错误处理

| 错误码 | 含义 | 本项目处理方式 |
|---|---|---|
| 401 | Token 无效/过期/未提供 | 提示用户重新申请 MCP Token 并更新连接器配置，终止流程 |
| 429 | 触发限流（>600 次/分钟） | 指数退避重试（1s → 2s → 4s，最多 3 次），并提示用户降低频率 |
| 其他 | 业务异常 | 保留已完成阶段的结果（报单表、券候选），向用户说明失败环节并给出降级建议 |

> 本项目所有 MCP 调用均为用户主动发起，不包含任何后台轮询、自动下单或未经确认的写操作。
