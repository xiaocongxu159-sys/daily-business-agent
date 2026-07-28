# 领星真实经营数据同步契约（0.6.0 规划）

## 1. 本文范围

本文固定 Daily Business Agent 后续接入领星真实经营数据时必须遵守的接口、字段、身份、分页、日期和失败处理边界。

当前稳定基线 `0.5.4` 只通过固定出口读取店铺列表。本文和同分支的合成测试不读取真实账号、不包含 AppID/AppSecret、代理凭据或客户数据，也不改变 `0.5.4` 的运行行为。

当前项目固定使用第三方开源 SDK `lingxingapi==2.1.7`。SDK 的方法和路由用于实现参考，但最终字段仍必须在受控真实账号验证中与领星开放平台返回值、领星页面口径进行核对。

## 2. 数据源结论

| 数据集 | 首选调用 | 状态 | 主要粒度 | 分页/任务 |
|---|---|---|---|---|
| 店铺 | `api.basic.Sellers` | SDK 已确认 | `sid` 对应的店铺/站点 | 通常一次返回 |
| Listing/身份映射 | `api.sales.Listings` | SDK 已确认 | `sid + msku` | `offset + length`，最大 1000 |
| 订单明细 | `api.source.Orders` | SDK 已确认 | `sid + amazon_order_id + msku` | `offset + length`，默认 1000 |
| Sessions/PV/业务报告 | `api.source.ExportReportTask` | 需要扩展 SDK | 日期 + 站点 + SKU/子 ASIN | 异步创建、轮询、下载 |
| 广告账号 | `api.ads.Profiles` | SDK 已确认 | `sid + profile_id` | 分页 |
| SP 商品广告日报 | `api.ads.SpProductReports` | SDK 已确认 | 日期 + 广告账号 + ASIN/MSKU | `next_token` |
| SB 广告日报 | `api.ads.SbCampaignReports` | SDK 已确认 | 日期 + 广告账号 + Campaign | `next_token` |
| SD 商品广告日报 | `api.ads.SdProductReports` | SDK 已确认 | 日期 + 广告账号 + ASIN | `next_token` |
| FBA 库存快照 | `api.warehouse.FbaInventory` | SDK 已确认 | 快照日期 + `sid + msku` | `offset + length` |
| FBA 共享库存详情 | `api.warehouse.FbaInventoryDetails` | 需真实账号核对 | 共享仓/商品当前明细 | `offset + length`，最大 200 |
| 月度销售计划/广告预算 | 未确认开放接口 | 暂不可接 | 月份 + 店铺 + 可选 SKU | 继续使用本地计划文件 |

## 3. 店铺和商品身份

### 3.1 店铺身份

店铺主键使用领星 `sid`，同时持久化：

- `seller_id`：Amazon 卖家 ID；
- `marketplace_id`：Amazon 站点 ID；
- `region`：`NA`、`EU` 或 `FE`；
- `seller_name`、`country` 仅用于展示，不能作为关联主键；
- `profile_id` 必须通过广告账号接口与 `sid` 建立明确映射。

店铺改名不能产生新店铺，两个名称相同的店铺也不能自动合并。

### 3.2 商品身份

商品稳定关联优先级：

1. `sid + msku`；
2. 在同一 `sid` 内使用 `asin`、`fnsku`、`lsku` 辅助核对；
3. 只有 ASIN 而没有店铺时，不得直接合并多店铺数据；
4. 父 ASIN 只用于展示和汇总层级，不替代子体/SKU 主键。

FBA 共享库存详情可能返回 `sid=0`。这类数据必须根据接口提供的多国本地可售列表进行受控分配，不能按店铺名称或 ASIN 猜测归属。

## 4. 销售和订单

### 4.1 首选订单源

订单明细首选 `api.source.Orders`：

- 路由：`/erp/sc/data/mws_report/allOrders`；
- 支持单店铺、日期范围和 `offset + length`；
- 可按下单日期或 Amazon 更新时间查询；
- 包含 `amazon_order_id`、状态、ASIN、MSKU、LSKU、数量、销售金额、币种、本地购买日期和更新时间。

### 4.2 增量策略

- 初次回补拆成有限日期窗口，不请求无限历史；
- 日常增量按 Amazon 更新时间查询；
- 默认重复拉取最近 3 天并幂等覆盖，吸收状态、取消、退款和金额修正；
- 看板日期取店铺所在站点的本地下单日期；
- 原始 UTC 时间和来源更新时间继续保存用于审计；
- 不允许简单追加导致同一订单重复累计。

销售额、订单量、销量的最终过滤口径必须用真实账号与领星页面逐日对账后锁定。尤其要确认取消、退款、税费、运费和促销折扣的处理方式。

## 5. Sessions 和 Page Views

Amazon 官方 Reports API 提供 `GET_SALES_AND_TRAFFIC_REPORT`，可返回销售、Sessions、Page Views 等按日期和 ASIN/SKU 聚合的数据。领星 SDK 提供通用异步报告导出方法：

- 创建：`/basicOpen/report/create/reportExportTask`；
- 查询：`/basicOpen/report/query/reportExportTask`；
- 下载链接续期：`/basicOpen/report/amazonReportExportTask`。

目标报告参数必须包含：

```json
{
  "reportType": "GET_SALES_AND_TRAFFIC_REPORT",
  "reportOptions": {
    "dateGranularity": "DAY",
    "asinGranularity": "SKU"
  }
}
```

当前 `lingxingapi 2.1.7` 的 `ExportReportTask` 只暴露 `seller_id`、`marketplace_ids`、`region`、`report_type`、开始和结束时间，没有暴露 `reportOptions`。因此：

1. 不能静默使用默认 `PARENT` 粒度；
2. 下一实现阶段必须为该请求增加公开、可测试的 `reportOptions` 支持；
3. 如果领星转发接口不接受 `SKU`，只能在真实账号验证后明确降级到 `CHILD`，并显示粒度提示；
4. 报告缺少日期、SKU/子 ASIN、Sessions 或 Page Views 时必须报“数据缺失”，不能填造零值。

同步建议按 7 至 30 天窗口创建任务，保存请求指纹并轮询结果；已成功下载的相同请求不得反复创建。最近 3 天可重复请求以吸收迟到修正。

## 6. 广告数据

### 6.1 广告账号

先读取广告账号并建立 `sid + profile_id` 映射。店铺没有广告授权时应显示“未授权/无数据”，不能当作广告指标全部为零。

### 6.2 日报来源

SP 商品报告已确认包含：

- `report_date`；
- `profile_id`、Campaign、Ad Group、Ad ID；
- `asin`、`msku`；
- `impressions`、`clicks`、`cost`、`orders`、`sales`。

SB 和 SD 使用各自日报接口。看板店铺汇总可先按 Campaign 日报计算；需要 SKU 级归因时，必须分别验证 SB 创意报告和 SD 商品报告的 ASIN 口径。

广告接口按单日请求并使用 `next_token` 翻页。归因数据会修正，默认重拉最近 14 天；该天数作为可配置工程默认值，真实账号对账后可调整。

## 7. FBA 库存

主快照使用 `api.warehouse.FbaInventory(sids)`，关键字段：

- 可售：`afn_fulfillable_qty`；
- 不可售：`afn_unsellable_qty`；
- 预留待调仓：`afn_reserved_fc_processing_qty`；
- 预留调仓中：`afn_reserved_fc_transfers_qty`；
- 预留客户订单：`afn_reserved_customer_order_qty`；
- 入库计划：`afn_inbound_working_qty`；
- 发货在途：`afn_inbound_shipped_qty`；
- 接收中：`afn_inbound_receiving_qty`。

规范化公式：

```text
reserved = processing + transfers + customer_order
inbound = working + shipped + receiving
on_hand = fulfillable + unsellable + reserved
total_with_inbound = on_hand + inbound
```

`afn_actual_shipped_qty` 暂时只保留作审计字段，在真实账号证明它不与 `afn_inbound_shipped_qty` 重叠前，不加入库存总数。

每次同步写一份不可变的“日期 + 店铺 + SKU”快照。跨日期数据绝不能相加后再写回每天；这条规则延续 `0.5.4` 已验收的库存趋势修复。

## 8. 月度计划

当前 SDK 未发现领星“经营计划/销售计划/广告预算”的已确认读取方法。处理规则：

- 不猜测私人接口；
- 不抓取领星网页；
- 不通过浏览器自动化绕过开放平台；
- 继续保留本地月度计划文件；
- 后续只有在领星官方文档或受控接口返回明确确认后，才增加自动同步。

## 9. 本地同步状态和失败保护

每个数据集分别维护：

- `status`：idle、syncing、success、partial、failed；
- `last_attempt_at`、`last_success_at`；
- 最近成功日期范围和分页/任务检查点；
- 经过脱敏的错误分类；
- 数据粒度与缺失字段提示。

写入流程必须是：临时文件/临时数据库事务 → 完整性校验 → 原子替换。单个数据集失败时：

- 保留该数据集上一次成功数据；
- 其他数据集可继续；
- 页面明确显示哪些数据过期或缺失；
- 不清空整个看板；
- 日志不得包含凭据、认证代理 URL 或报告下载签名参数。

## 10. 开发门禁

### 当前契约阶段

- 只提交契约、文档和合成测试；
- 不调用真实领星账号；
- 不修改 `0.5.4` 稳定 Agent；
- 不改变固定出口、DPAPI、连接包或浏览器插件。

### 下一实现阶段

必须增加：

1. 通用 offset 和 next-token 分页器；
2. 异步报告任务状态机和安全下载器；
3. `reportOptions` 请求扩展；
4. 本地数据集检查点和原子提交；
5. 合成响应字段验证、迟到修正和幂等测试；
6. 网络门禁，确保所有领星请求仍只经过固定出口；
7. 脱敏日志测试。

### 真实账号受控验证

只有自动测试通过后才需要一次人工协助：在公司电脑上运行“只读字段探测”，仅汇报接口是否授权、返回字段名、数据条数和日期范围，不展示 AppID/AppSecret、代理信息、订单号或业务明细。探测通过后才进入真实数据同步开发。
