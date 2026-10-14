---
name: mcd-group-order
description: >
  麦当劳拼单分账助手「麦麦拼单官」。当用户提到「拼单」「一起点麦当劳」「帮我们点麦当劳」
  「AA 分账」「算一下谁该给谁多少钱」「公司/宿舍团餐」「麦乐送凑单」「用哪张券最省」
  等多人共同点麦当劳的场景时触发。支持：解析群聊报单文本、匹配门店菜单、枚举并试算最优
  优惠券组合、调用麦当劳 MCP 下单、按三种分账口径精确分摊（精确到分、总额恒等）、
  生成可截图发群的分账卡片。单人点餐请勿触发本技能。
---

# 麦麦拼单官 · 多人拼单编排与分账

把「一群人在群里报单 → 一个人下单 → 事后算钱」这条真实但繁琐的链路，压缩成一次对话。

## 核心能力

| 能力 | 说明 |
|---|---|
| 报单解析 | 群聊里七嘴八舌的文本 → 结构化报单（本地脚本，不消耗 MCP 配额） |
| 菜单匹配 | 口语化餐品名 → 门店餐品编码（本地模糊匹配 + 人工兜底） |
| 券组合优化 | 建立「券 × 商品」映射 → `calculate-price` 权威试算 → 选出最省方案 |
| 精确分账 | 最大余额法分摊，**Σ(每人应付) 恒等于订单实付**，永不出现 0.01 尾差 |
| 分账卡片 | 生成自包含 HTML，截图即可发群 |

## 麦当劳 MCP 实测要点（务必先读，参数错了会直接失败）

以下均来自**真实调用验证**，不是文档推测：

### 1. 金额单位是「分」

`calculate-price` 返回的 `data.discount` / `data.price` / `data.deliveryPrice` /
`data.packingPrice` / `data.productList[].originalSubtotal` **全部以分为单位**（整数）。
展示给用户前除以 100。本地脚本 `split_bill.py` / `from_price.py` 内部也用分，
所以从算价到分账全程**不要做单位换算**，避免二次舍入。

### 2. 返回格式不统一：有的返回 JSON，有的返回 Markdown

| 工具 | 实际返回 |
|---|---|
| `now-time-info` / `query-nearby-stores` / `query-meals` / `calculate-price` / `query-store-coupons` | JSON |
| `query-my-coupons` / `available-coupons` / `campaign-calendar` | **Markdown 文本** |

因此：**先把 Markdown 人工/由你解析成结构化 JSON，再喂给本地脚本**。
本地脚本只接受结构化 JSON，不要指望它们去解析 Markdown。

### 3. `query-meals` 的菜单是「以编码为 key 的字典」

```
data.meals = { "1100": {name, currentPrice, originalPrice, ...}, ... }
```

**不是数组**。而 `data.categories[].meals[]` 里只有 `code` 和 `tags`、**没有名称**，
所以取名称必须以 `data.meals` 为准。价格字段是 `currentPrice`（字符串，单位元）。
（`match_menu.py` 已兼容此结构，直接用整个响应即可。）

### 4. 优惠券是「按商品绑定」的，不是满减券

`query-store-coupons` 每条券的实际结构：

```
{ "title": "中薯条买一送一", "couponId": "...", "couponCode": "...",
  "tradeDateTime": "...", "products": [{"productName": "中薯条", "productCode": "4810"}] }
```

**没有面额、没有门槛**。所以不要向用户承诺「预计省 X 元」——
真实优惠额只有 `calculate-price` 知道（`data.enjoyed.realDiscount` / `data.discount`）。

### 5. 到店自取不传 `beCode`，得来速必须传

- `beType=1`（到店自取）：返回的门店**没有 `beCode`**，后续调用**不传** `beCode`
- `beType=5`（得来速）：返回的门店**有 `beCode`**，后续调用**必须传**
- `beType=2`（麦乐送）/ `beType=6`（企业团餐）：`orderType=2`，必须传 `beCode`

`orderType`：到店场景（含得来速）为 `1`，外送场景（含团餐）为 `2`。

### 6. 收藏门店为空会报错

`query-nearby-stores(searchType=1)` 在用户没有收藏门店时返回
`code=600050 "收藏餐厅列表为空"`。此时改用 `searchType=2` 并传 `city` + `keyword`。

## 前置检查

开始前必须确认：

1. **就餐方式**：到店自取 / 麦乐送外送 / 得来速 / 企业团餐。
2. **门店或地址**：到店需要城市+关键词定位门店；外送需要配送地址。
3. **报单文本**：用户粘贴的群聊记录，或逐条录入。
4. **垫付人**：谁先付的钱（用于生成转账方案），缺省为第一个报单的人。

若麦当劳 MCP 连接器未启用，提示用户按 `mcp-config.example.json` 配置 `mcd-mcp` 后重试，
**不要**用任何替代数据伪造菜单或价格。

## 工作流

### 阶段 1 · 定位门店

**必须按实测参数调用**：

```
到店自取：query-nearby-stores(searchType=1, beType=1)
          → 若报 600050，改 query-nearby-stores(searchType=2, city, keyword, beType=1)
得来速  ：query-nearby-stores(searchType=2, city, keyword, beType=5)   # 门店带 beCode
外送    ：delivery-query-addresses → (delivery-create-address) → delivery-query-stores
团餐    ：同外送，额外用 query-meal-assistance 确认助餐服务
```

展示门店时必须完整给出 `storeCode`（以及 beType=5 时的 `beCode`），让用户选择，
**不要替用户决定**。

### 阶段 2 · 解析报单并匹配菜单

1. 把群聊文本写入临时文件，调用本地脚本：

   ```bash
   python scripts/parse_orders.py -i <chat.txt> --pretty
   ```

2. 调用 `query-meals(storeCode, orderType, beType)` 拉取门店菜单，把**整个响应**存成 JSON。

3. 组装 `{"orders": <上一步输出>, "menu": <query-meals 响应>}` 后调用：

   ```bash
   python scripts/match_menu.py -i <merged.json>
   ```

4. **检查 `unmatched` 列表**：脚本不敢猜的条目必须逐一与用户确认，确认后再补齐餐品编码。
   **绝不允许**把没匹配上的条目用相似度最高的结果硬凑——价格错了整个分账就废了。

5. 对有备注的条目，按需调用 `query-meal-detail` 确认套餐可替换项，判断备注是否可实现。
   注意：特制项要用 `modification.values[].code` + `key`（选中用 `selectedKey`，
   未选中但 `unselectedKey` 非空的也必须传），传法错了算价会不准。

### 阶段 3 · 券组合优化

1. 并行拉取券数据：
   - `query-store-coupons(storeCode, orderType, beType)` —— **当前门店可用券**（返回 JSON）
   - `query-my-coupons(page, pageSize)` —— 用户卡包券（返回 Markdown，需先转 JSON）
   - `available-coupons` —— 麦麦省可领券（返回 Markdown）
2. `campaign-calendar` 看当月活动（返回 Markdown），判断是否有比券更划算的活动。
3. 把券列表与购物车（含 `productCode`）组装后调用：

   ```bash
   python scripts/plan_coupons.py -i <coupons.json> --top-k 8
   ```

   输出里 `usable` 是能用的券，`unusable` 是本单用不上的券（附带原因，可告知用户）。
4. 对 `candidates` 中的**每一个组合**调用 `calculate-price` 试算：
   - 把候选 `apply[]` 里的 `couponId` / `couponCode` 挂到对应 `productCode` 的 item 上
   - 取 `data.price`（分）最低者为最终方案
   - 试算次数 ≤8 次（脚本已剪枝）
5. **不要**声称「预计省 X 元」，一切优惠金额以 `calculate-price` 的返回为准。

### 阶段 4 · 分账（核心）

用**最终选定方案**的 `calculate-price` 响应，加上报单归属，转成分账输入：

```bash
python scripts/from_price.py --price <price.json> --orders <orders.json> \
    --out bill.json --payer "<垫付人>"
```

`orders.json` 形如 `[{"person": "张三", "productCode": "1100", "qty": 1}]`。

该脚本会自动：
- 把每个商品的原价小计按各人数量比例**精确分摊**（最大余额法，除不尽也不会产生尾差）
- 透传 `discount` / `deliveryPrice` / `packingPrice`（单位分）
- **自检**：由明细推出的应付必须等于算价接口给出的 `price`，不等则报警

**分账口径必须先问用户**，不要默认替用户决定：

| 口径 | 适用场景 |
|---|---|
| `proportional` 按商品原价比例 | 默认推荐。买得多的人分摊更多优惠，最公平 |
| `equal` 按人头均摊 | 大家金额差不多，图省事 |
| `owner` 谁用券谁受益 | 券是某人自己的，优惠归他 |

然后分账：

```bash
python scripts/split_bill.py -i bill.json --markdown
```

生成卡片：

```bash
python scripts/render_card.py -i <split.json> -o <out.html> \
  --store "<门店名>" --order-no "<订单号>" --time "<时间>"
```

**必须调用 `present_files` 展示卡片**，让用户能直接看到并截图。

### 阶段 5 · 下单（写操作，必须二次确认）

在用户明确确认方案与金额后调用 `create-order`。注意：
- 取餐方式 code 取自 `calculate-price` 返回的 `data.takeWayList[]`（如 `eat-in` 堂食、
  `take-in-store` 外带），**不要自己编**。
- 企业团餐还要带 `gmServiceCode`（取自 `data.mealAssistanceList[]`）。
- 下单成功后用 `query-order` 跟踪状态，把订单号回填到分账卡片，再 `present_files` 一次。

## 关键约束

1. **写操作必须确认**：`create-order`、`auto-bind-coupons`、`delivery-create-address`、
   `cancel-order` 一律先说明将要发生什么，得到用户明确同意后再执行。
2. **不碰用户的积分资产**：不调用 `draw-lottery`、`mall-create-order` 等消耗积分的工具。
3. **Token 不落盘**：任何情况下不得把 MCP Token 写入文件、日志或输出中。
4. **金额以 MCP 为准**：所有价格、优惠额一律取自 `calculate-price` 的返回。
   菜单里的 `currentPrice` 只用于匹配阶段预估，不得作为最终账单依据。
5. **单位不要反复换算**：从算价到分账全程用「分」，只在展示时转「元」。
6. **分账必须过校验**：`split_bill.py` 内部会做总额自检，若抛出 `AssertionError`，
   必须回到上一步排查，**不得**跳过校验输出结果。`from_price.py` 的 `_warnings`
   也要如实转达给用户，不能吞掉。
7. **不编造数据**：菜单、券、价格取不到时如实告知用户，不用示例数据顶替。
8. **对外口径**：卡片页脚已包含「非麦当劳官方产品」声明，不要删除。

## 输出规范

最终回复应包含：

1. **最优方案**：用了哪张券、省了多少（取自算价返回）、实付多少。
2. **分账表**：谁、原价、分摊优惠、应付。
3. **转账清单**：谁转给谁多少钱。
4. **分账卡片**：通过 `present_files` 展示。

## 参考

- 报单文本格式与解析规则：`references/order-format.md`
- 分账算法与输入结构：`references/split-algorithm.md`
- MCP 工具映射与调用流程：仓库根目录 `MCP_INTEGRATION.md`
