# 分账算法与输入结构

## 为什么需要专门的算法

拼单分账有三个真实痛点：

1. **除不尽**。3 个人均摊 10 元配送费，每人 3.333… 元，四舍五入后合计 9.99 或 10.02，账对不上。
2. **优惠怎么分**。满 100 减 20，是均摊每人省 4 元？还是买得多的人省得多？还是谁用券谁受益？
   这是拼单群里最容易吵架的地方。
3. **算错了没人知道**。手算分摊，少一分多一分都不会被发现，但心里会不舒服。

本项目用**最大余额法**解决第 1 个问题，用**可配置口径**解决第 2 个问题，
用**总额自检**解决第 3 个问题。

---

## 最大余额法（Largest Remainder Method / Hamilton 法）

设待分摊总额为 $T$（整数分），第 $i$ 个人的权重为 $w_i$，权重和为 $W = \sum w_i$。

1. 计算理论份额：$r_i = T \cdot w_i / W$
2. 先取下取整：$b_i = \lfloor r_i \rfloor$
3. 计算余数：$R = T - \sum b_i$（数学上 $0 \le R < n$）
4. 按小数部分 $r_i - b_i$ **从大到小**排序，前 $R$ 个人各加 1 分

**性质**：$\sum (b_i + \Delta_i) = T$ 恒成立，即分摊之和**精确等于**待分摊总额。
平局时按索引升序分配，保证结果可复现（不依赖字典/集合的迭代顺序）。

**示例**：$T = 1000$ 分（10.00 元），3 人权重相同

- $r_i = 333.33$，$b_i = 333$，$\sum b_i = 999$，$R = 1$
- 小数部分相同，索引最小者 +1 → `[334, 333, 333]`，合计 1000 ✓

---

## 三种分账口径

| 口径 | `discount_policy` | 权重 $w_i$ | 适用场景 |
|---|---|---|---|
| 按比例 | `proportional`（默认） | 各人商品原价 | 买得多的人分摊更多优惠，最公平 |
| 均摊 | `equal` | 全为 1 | 大家金额差不多，图省事 |
| 谁用券谁受益 | `owner` | 券持有人为 1，其余为 0 | 券是某人自己的 |

费用（配送费 + 打包费）单独有一套口径：

| 口径 | `fee_policy` | 说明 |
|---|---|---|
| 均摊 | `equal`（默认） | 配送费按人头分 |
| 按比例 | `proportional` | 配送费按商品原价分 |

### 上界保护

优惠分摊时施加「单人优惠 ≤ 本人商品原价」的上限，用**注水法**在未触顶的人之间重新分配。
这样即使优惠总额异常大于商品总额，也不会出现负的应付金额；无法分摊的溢出部分
记入 `summary.overflow` 并在 `note` 中提示。

---

## 输入结构

```json
{
  "payer": "张三",
  "discount": 20.0,
  "delivery_fee": 9.0,
  "packaging_fee": 0,
  "discount_policy": "proportional",
  "fee_policy": "equal",
  "discount_owner": "李四",
  "note": "使用「满 100 减 20」券",
  "items": [
    { "person": "张三", "name": "麦辣鸡腿堡套餐", "unit_price": 32.0, "qty": 1 }
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `items` | ✅ | 商品行；`unit_price` 单位为元，支持 `price` 作为别名 |
| `payer` | | 垫付人，缺省取第一个报单的人 |
| `discount` | | 优惠总额（正数）；请取 `calculate-price` 返回的优惠金额 |
| `delivery_fee` / `packaging_fee` | | 费用，缺省 0 |
| `discount_policy` | | `proportional` / `equal` / `owner`，缺省 `proportional` |
| `fee_policy` | | `equal` / `proportional`，缺省 `equal` |
| `discount_owner` | | 仅 `owner` 口径需要；缺失或人不在报单里会自动退回 `proportional` 并提示 |

---

## 输出结构

```json
{
  "policy": { "discount": "proportional", "fee": "equal" },
  "summary": {
    "subtotal": "168.90", "discount": "20.00",
    "delivery_fee": "9.00", "packaging_fee": "0.00",
    "payable": "157.90", "saved": "20.00",
    "payer": "张三", "overflow": "0.00"
  },
  "shares": [
    {
      "person": "张三", "subtotal": "32.00", "discount": "3.79",
      "fee": "1.80", "payable": "30.01", "items": ["麦辣鸡腿堡套餐×1"]
    }
  ],
  "transfers": [
    { "from": "李四", "to": "张三", "amount": "42.35" }
  ],
  "note": ""
}
```

## 不变量（代码中会强制校验）

```
Σ shares[i].payable == summary.payable
summary.payable == summary.subtotal - summary.discount
                  + summary.delivery_fee + summary.packaging_fee
```

任一条不成立，`SplitResult.verify()` 会抛出 `AssertionError`，流程必须中止并回查输入，
**不允许**带着不自洽的账单继续输出。

测试见 `tests/test_split_bill.py`，包含 1500 组随机模糊测试。
