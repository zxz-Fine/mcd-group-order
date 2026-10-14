---
name: mcd-group-order
description: >
  麦当劳拼单分账助手「麦麦拼单官」。当用户提到「拼单」「一起点麦当劳」「帮我们点麦当劳」
  「AA 分账」「算一下谁该给谁多少钱」「公司/宿舍团餐」「麦乐送凑单」「用哪张券最省」
  「随便」「都行」这类没指定餐品的报单等多人共同点麦当劳的场景时触发。支持：解析群聊
  报单文本、匹配门店菜单、把「随便」指派为具体汉堡（多人尽量不重样）、逐张试算优惠券
  并选出最省的一张、调用麦当劳 MCP 下单、按三种分账口径精确分摊（精确到分、总额恒等）、
  生成可截图发群的分账卡片。单人点餐请勿触发本技能。
---

# 麦麦拼单官 · 多人拼单编排与分账

把「一群人在群里报单 → 一个人下单 → 事后算钱」这条真实但繁琐的链路，压缩成一次对话。

## 核心能力

| 能力 | 说明 |
|---|---|
| 报单解析 | 群聊里七嘴八舌的文本 → 结构化报单（本地脚本，不消耗 MCP 配额） |
| 菜单匹配 | 口语化餐品名 → 门店餐品编码（本地模糊匹配 + 人工兜底） |
| 泛称指派 | 「随便」类报单 → 自动指派汉堡，多人尽量不重样，并避开忌口 |
| 单券择优 | 建立「券 × 商品」映射 → 逐张 `calculate-price` 权威试算 → 选出最省的那张（**一次只能用 1 张券**） |
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

`query-store-coupons` 每条券的实际结构（真实返回）：

```
{ "title": "中薯条买一送一", "couponId": "...", "couponCode": "...",
  "tradeDateTime": "2026-10-12 10:30:00-2026-10-16 23:59:59",
  "products": [{"productCode": "9900012387", "productName": "中薯条买一送一"}] }
```

**没有面额、没有门槛**。所以不要向用户承诺「预计省 X 元」——
真实优惠额只有 `calculate-price` 知道（`data.enjoyed.realDiscount` / `data.discount`）。

### 5. ⚠️ 算价必须用**券自己的** `productCode`，不是菜单里的编码

券里的 `99xxxxxxxx` 是**活动虚拟商品编码**，购物车里不会出现：

| 券名 | 券编码（**算价必须用它**） | 菜单编码 |
|---|---|---|
| 中薯条买一送一 | `9900012387` | `4810` |
| 9.9元中杯冰美式 | `507411` | `507411`（恰好相同） |

**实测结论（2026-10-14，同一购物车分别试挂）**：

| 挂法 | 结果 |
|---|---|
| 「中薯条买一送一」挂 `4810` | ❌ `code=600012「促销规则不支持该商品」` |
| 「中薯条买一送一」挂 `9900012387` | ✅ ¥71.50 → **¥50.90** |
| 「9.9元中杯冰美式」挂 `507411` | ✅ ¥58.00 → **¥48.90** |

所以 `calculate-price` 的 `items[].productCode` **必须填券自己的编码**
（即 `apply[].productCode`），填菜单里的等价商品编码会直接失败。
`apply[].cartProductCode` 是购物车编码，**只用于分账归属，不要传给算价接口**。

**券商品是「券的商品形态」，不是别名。** 例如「中薯条买一送一」= 2 份中薯条按 1 份计价
（原价 2700 分、券后 1350 分），与用户报单的「中薯条 ×1」并不是同一件事。
下单前**必须与用户确认**商品形态是否一致。

- **匹配侧**：`plan_coupons.py` 用三级降信匹配（`exact_code` 1.0 → `name_exact` 0.9 →
  `name_contains` 0.7）把券关联到购物车商品；名称匹配前会剥离「买一送一 / 第二杯半价 /
  9.9元」等营销词。`apply[].match` 不是 `exact_code` 时会显式告警。
- **分账侧**：用 `from_price.py --code-alias 9900012387=4810` 把券商品编码映射回报单编码，
  否则该商品会因「无归属人」被挂到占位人名下。

### 6. 一次只能使用 1 张券（**不支持叠加**）

实测：同一 `calculate-price` 请求挂 2 张券 → `code=600022「暂不支持多张券使用」`。

所以**不存在「券组合优化」**，只有「单券择优」——对每张可用券各试算一次，
取 `data.price` 最低者。`plan_coupons.py` 的 `max_stack` 已强制为 1。

### 7. 门店存在自动促销，且与券叠加

同一杯冰美式：不挂券 `1900 → 1190`（门店自动减 710 分）；挂 9.9 元券后 `→ 990`
（再减 200 分）。**不要本地推算优惠额，必须实际调用 `calculate-price`。**

### 8. 到店自取不传 `beCode`，得来速必须传

- `beType=1`（到店自取）：返回的门店**没有 `beCode`**，后续调用**不传** `beCode`
- `beType=5`（得来速）：返回的门店**有 `beCode`**，后续调用**必须传**
- `beType=2`（麦乐送）/ `beType=6`（企业团餐）：`orderType=2`，必须传 `beCode`

`orderType`：到店场景（含得来速）为 `1`，外送场景（含团餐）为 `2`。

### 9. 收藏门店为空会报错

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

### 阶段 2.5 · 指派「随便」类报单

群里总有人只回一句「随便」。这类报单**没有具体餐品**，硬去匹配菜单只会误伤，
所以 `parse_orders.py` 会给它打上 `intent: "any"`，`match_menu.py` 跳过匹配，
由 `assign_any.py` 指派一个具体汉堡：

```bash
python scripts/assign_any.py -i <matched.json> --menu <menu.json> --kind burger
```

输入需含 `items`；菜单可来自输入文件的 `menu` 字段，或用 `--menu` 单独指定。

指派规则：

| 规则 | 说明 |
|---|---|
| 默认品类 | 汉堡（`--kind burger`） |
| **不重样** | 第 k 个「随便」拿候选池里第 k 个**未分配**的汉堡，多人不会撞口味 |
| **避开已点** | 有人明确点过巨无霸，其他人「随便」优先跳过它 |
| **忌口** | 备注含「不要辣 / 不吃牛 / 不要鱼」时排除对应汉堡 |
| **x2 拆分** | 「随便 x2」→ 2 个**不同口味**的汉堡 |
| 确定性 | 按菜单顺序轮换，不引入随机数，同一输入永远得到同一结果 |

**必须「列出来问」**：把输出里的 `confirm_message`（已生成好的逐条清单）原样发给用户，
形如——

```
以下 5 份汉堡是根据「随便」自动安排的，请确认有没有不吃的：
  · 小孙 → 板烧鸡腿堡 ¥21.50（备注：不要辣）
  · 周七 → 麦辣鸡腿堡 ¥21.00
  · 小李 → 双层吉士汉堡 ¥23.00
  · 小赵 → 麦香鱼 ¥22.50
  · 小赵 → 麦香鸡 ¥18.00
共 5 种口味，无重复。
（巨无霸已按明确点单保留，未参与指派。）
```

用户确认后才继续。若 `warnings` 提示「候选数量少于随便人数」或「忌口未能满足」，
更要显式说明，不要静默下单。用户说不吃某个口味时，**改为让他自己指定**，不要重新指派。

> 边界处理：菜单里识别不到汉堡时**明确告警而非编造餐品**；忌口把候选排光时会回退
> 并告警，交用户定夺。

### 阶段 3 · 单券择优（**没有「组合」**）

> 实测：`calculate-price` 一次只接受 1 张券，挂 2 张直接返回
> `code=600022「暂不支持多张券使用」`。所以本阶段是「逐张试算、取最低」，不是组合优化。

1. 并行拉取券数据：
   - `query-store-coupons(storeCode, orderType, beType)` —— **当前门店可用券**（返回 JSON）
   - `query-my-coupons(page, pageSize)` —— 用户卡包券（返回 Markdown，需先转 JSON）
   - `available-coupons` —— 麦麦省可领券（返回 Markdown）
2. `campaign-calendar` 看当月活动（返回 Markdown），判断是否有比券更划算的活动。
3. 把券列表与购物车（含 `productCode`）组装后调用：

   ```bash
   python scripts/plan_coupons.py -i <coupons.json> --top-k 8
   ```

   输出里 `usable` 是能用的券，`unusable` 是本单用不上的券（附带原因，可告知用户），
   `candidates` 是「不使用券基线 + 每张券一个候选」。
4. 对 `candidates` 中**每个候选**（含基线）调用 `calculate-price` 试算：
   - 把 `apply[].couponId` / `couponCode` 与 **`apply[].productCode`（券自己的编码）**
     组装进 `items[]` —— 注意**不是** `apply[].cartProductCode`
   - 取 `data.price`（分）最低者为最终方案
   - 试算次数 ≤ 券数 + 1，一般 2~5 次
5. 若某张券的 `apply[].productCode` 与 `apply[].cartProductCode` 不同，
   说明它是**券专属商品形态**（如「买一送一」= 2 份），**必须先与用户确认**再下单。
6. **不要**声称「预计省 X 元」，一切优惠金额以 `calculate-price` 的返回为准。

### 阶段 4 · 分账（核心）

用**最终选定方案**的 `calculate-price` 响应，加上报单归属，转成分账输入：

```bash
python scripts/from_price.py --price <price.json> --orders <orders.json> \
    --out bill.json --payer "<垫付人>" \
    --code-alias <券商品编码>=<购物车编码>
```

`orders.json` 形如 `[{"person": "张三", "productCode": "1100", "qty": 1}]`。

**用了券就一定要传 `--code-alias`**：挂券后 `calculate-price` 会按**券自己的编码**返回商品
（如「中薯条买一送一」返回 `9900012387`），而报单里写的是购物车编码（中薯条 `4810`）。
不传别名，该商品会因「无归属人」被挂到 `__未分配__` 占位人名下。
别名可重复传多次，映射关系取自 `plan_coupons.py` 输出的
`apply[].productCode` → `apply[].cartProductCode`。

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
9. **泛称指派必须让用户确认**：`assign_any.py` 指派的口味是**推测**而非用户明示。
   必须把 `assignments` 展示给用户确认后才可下单；`warnings` 里关于「重复口味」或
   「忌口未满足」的提示不得省略。菜单里识别不到汉堡时**不得编造餐品**。

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
