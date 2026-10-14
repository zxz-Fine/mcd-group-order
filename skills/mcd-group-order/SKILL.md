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
| 券组合优化 | 枚举候选组合 → `calculate-price` 权威试算 → 选出最省方案 |
| 精确分账 | 最大余额法分摊，**Σ(每人应付) 恒等于订单实付**，永不出现 0.01 尾差 |
| 分账卡片 | 生成自包含 HTML，截图即可发群 |

## 前置检查

开始前必须确认：

1. **就餐方式**：到店取餐 / 麦乐送外送 / 得来速。
2. **门店或地址**：到店需要地址定位门店；外送需要配送地址。
3. **报单文本**：用户粘贴的群聊记录，或逐条录入。
4. **垫付人**：谁先付的钱（用于生成转账方案），缺省为第一个报单的人。

若麦当劳 MCP 连接器未启用，提示用户按 `mcp-config.example.json` 配置 `mcd-mcp` 后重试，**不要**用任何替代数据伪造菜单或价格。

## 工作流

### 阶段 1 · 定位门店

- 到店：调用 `query-nearby-stores`。
- 外送：`delivery-query-addresses` → 若地址为空则 `delivery-create-address` → `delivery-query-stores`。
- 企业团餐：额外调用 `query-meal-assistance` 确认门店是否支持助餐服务。

多个候选门店时，让用户选择，不要替用户决定。

### 阶段 2 · 解析报单并匹配菜单

1. 把群聊文本写入临时文件，调用本地脚本：

   ```bash
   python scripts/parse_orders.py -i <chat.txt> --pretty
   ```

2. 调用 `query-meals` 拉取门店菜单，保存为 JSON。

3. 调用本地脚本做匹配：

   ```bash
   python scripts/match_menu.py -i <merged.json>
   ```

   其中 `<merged.json>` 结构为 `{"orders": [...], "menu": [...]}`（`orders` 取上一步输出，`menu` 取 `query-meals` 的返回）。

4. **检查 `unmatched` 列表**：脚本不敢猜的条目必须逐一与用户确认，确认后再补齐餐品编码。
   **绝不允许**把没匹配上的条目用相似度最高的结果硬凑——价格错了整个分账就废了。

5. 对有备注的条目，按需调用 `query-meal-detail` 确认套餐可替换项，判断备注是否可实现
   （例如「不要生菜」在套餐里是否支持）。

### 阶段 3 · 券组合优化（核心）

1. 并行拉取券数据：`available-coupons`、`query-store-coupons`、`query-my-coupons`。
2. 同时调用 `campaign-calendar`，看当月是否有比券更划算的活动（如 1+1、第二份半价）。
3. 汇总券列表 + 商品小计，调用本地脚本生成候选：

   ```bash
   python scripts/plan_coupons.py -i <coupons.json> --top-k 8
   ```

4. 对候选中的**每一个组合**调用 `calculate-price` 试算，取应付总价最低者。
   - 试算次数控制在 ≤8 次（脚本已剪枝）。
   - 若 `plan_coupons.py` 返回了 `near_miss`（凑单提示），把净收益为正的建议告诉用户，
     由用户决定是否凑单，不要擅自加商品。
5. 若用户同意先领券再下单，调用 `auto-bind-coupons`，**然后重新试算**。

### 阶段 4 · 分账（核心）

用阶段 3 选定的方案（优惠总额、配送费、打包费）构造分账输入：

```bash
python scripts/split_bill.py -i <bill.json> --markdown
```

**分账口径必须先问用户**，不要默认替用户决定：

| 口径 | 适用场景 |
|---|---|
| `proportional` 按商品原价比例 | 默认推荐。买得多的人分摊更多优惠，最公平 |
| `equal` 按人头均摊 | 大家金额差不多，图省事 |
| `owner` 谁用券谁受益 | 券是某人自己的，优惠归他 |

配送费/打包费口径：`equal`（默认）或 `proportional`。

生成卡片：

```bash
python scripts/render_card.py -i <split.json> -o <out.html> \
  --store "<门店名>" --order-no "<订单号>" --time "<时间>"
```

然后**必须调用 `present_files` 展示卡片**，让用户能直接看到并截图。

### 阶段 5 · 下单（写操作，必须二次确认）

在用户明确确认方案与金额后，调用 `create-order`。下单成功后：

1. `query-order` 跟踪状态，把订单号回填到分账卡片。
2. 再次 `present_files` 更新后的卡片。

## 关键约束

1. **写操作必须确认**：`create-order`、`auto-bind-coupons`、`delivery-create-address`、
   `cancel-order` 一律先说明将要发生什么，得到用户明确同意后再执行。
2. **不碰用户的积分资产**：不调用 `draw-lottery`、`mall-create-order` 等消耗积分的工具。
3. **Token 不落盘**：任何情况下不得把 MCP Token 写入文件、日志或输出中。
4. **金额以 MCP 为准**：所有价格、优惠额一律取自 `calculate-price` 的返回，
   脚本里的价格仅用于匹配阶段，不得作为最终账单依据。
5. **分账必须过校验**：`split_bill.py` 内部会做总额自检，若抛出 `AssertionError`，
   说明输入数据有问题，必须回到上一步排查，**不得**跳过校验输出结果。
6. **不编造数据**：菜单、券、价格取不到时如实告知用户，不用示例数据顶替。
7. **对外口径**：卡片页脚已包含「非麦当劳官方产品」声明，不要删除。

## 输出规范

最终回复应包含：

1. **最优方案**：用了哪张券、省了多少、实付多少。
2. **分账表**：谁、原价、分摊优惠、应付。
3. **转账清单**：谁转给谁多少钱。
4. **分账卡片**：通过 `present_files` 展示。

## 参考

- 报单文本格式与解析规则：`references/order-format.md`
- 分账算法与输入结构：`references/split-algorithm.md`
- MCP 工具映射与调用流程：仓库根目录 `MCP_INTEGRATION.md`
