# 领星真实经营数据同步契约（0.6.0 规划）

## 1. 范围

本文固定 Daily Business Agent 后续接入领星真实经营数据时必须遵守的接口、字段、身份、分页、日期和失败处理边界。

当前稳定基线 `0.5.4` 只通过固定出口读取店铺列表。本契约不调用真实账号，不包含 AppID/AppSecret、代理凭据或客户数据，也不改变 `0.5.4` 的运行行为。

项目当前固定使用第三方开源 SDK `lingxingapi==2.1.7`。SDK 方法和路由用于实现参考；最终字段和经营口径仍须通过受控真实账号与领星页面逐日核对。

## 2. 数据源矩阵

| 数据集 | 首选调用 | 状态 | 粒度 | 分页/任务 |
|---|---|---|---|---|
| 店铺 | `api.basic.Sellers` | SDK 已确认 | 店铺/站点 | 通常一次返回 |
| Listing/身份 | `api.sales.Listings` | SDK 已确认 | `sid + msku` | `offset + length` |
| 订单明细 | `api.source.Orders` | SDK 已确认 | `sid + amazon_order_id + msku` | `offset + length` |
| Sessions/PV | `api.source.ExportReportTask` | 需要 SDK 扩展 | 日期 + 站点 + SKU/子 ASIN | 异步任务 |
| 广告账号 | `api.ads.AdProfiles` | SDK 已确认 | `sid + profile_id` | 分页 |
| SP 商品日报 | `api.ads.SpProductReports` | SDK 已确认 | 日期 + 广告账号 + ASIN/MSKU | `next_token` |
| SB 广告日报 | `api.ads.SbCampaignReports` | SDK 已确认 | 日期 + 广告账号 + Campaign | `next_token` |
| SD 商品日报 | `api.ads.SdProductReports` | SDK 已确认 | 日期 + 广告账号 + ASIN | `next_token` |
| FBA 库存 | `api.warehouse.FbaInventory` | SDK 已确认 | 快照日期 + `sid + msku` | `offset + length` |
| FBA 共享库存 | `api.warehouse.FbaInventoryDetails` | 需受控核对 | 共享仓/商品当前明细 | `offset + length` |
| 月度计划/预算 | 未确认开放接口 | 暂不可接 | 月份 + 店铺 + 可选 SKU | 本地计划文件 |

## 3. 身份规则

### 店铺

店铺主键使用 `sid`，同时保存：

- `seller_id`；
- `marketplace_id`；
- `region`；
- `seller_name`、`country` 仅用于显示；
- `profile_id` 必须通过 `api.ads.AdProfiles` 与 `sid` 建立明确映射。

店铺改名不能生成新店铺，名称相同也不能自动合并。

### 商品

稳定关联优先级：

1. `sid + msku`；
2. 同一 `sid` 内用 `asin`、`fnsku`、`lsku` 辅助核对；
3. 只有 ASIN 而没有店铺时，不得合并多店铺数据；
4. 父 ASIN 只用于展示和汇总层级。

共享库存详情可能返回 `sid=0`。这类数据不能按名称或 ASIN 猜测归属，必须受控验证接口返回的多国本地可售列表。

## 4. 销售和订单

订单首选 `api.source.Orders`：

- 路由 `/erp/sc/data/mws_report/allOrders`；
- 支持单店铺、日期范围和 offset 分页；
- 可按下单日期或 Amazon 更新时间查询；
- 包含订单号、状态、ASIN、MSKU、LSKU、数量、销售金额、币种、本地购买日期和更新时间。

增量规则：

- 首次回补拆成有限窗口；
- 日常按 Amazon 更新时间查询；
- 默认重复拉取最近 3 天并幂等覆盖；
- 看板日期取站点本地下单日期；
- 原始 UTC 时间和来源更新时间保留用于审计；
- 禁止简单追加造成重复累计。

取消、退款、税费、运费和促销折扣口径必须用真实账号与领星页面对账后锁定。

## 5. Sessions 和 Page Views

目标报告为 Amazon Reports API 的 `GET_SALES_AND_TRAFFIC_REPORT`。领星 SDK 提供异步报告通道：

- 创建：`/basicOpen/report/create/reportExportTask`；
- 查询：`/basicOpen/report/query/reportExportTask`；
- 下载链接续期：`/basicOpen/report/amazonReportExportTask`。

目标参数必须包含：

```json
{
  "reportType": "GET_SALES_AND_TRAFFIC_REPORT",
  "reportOptions": {
    "dateGranularity": "DAY",
    "asinGranularity": "SKU"
  }
}
```

当前 SDK 的 `ExportReportTask` 没有暴露 `reportOptions`，因此：

1. 禁止静默使用默认父 ASIN 粒度；
2. 下一阶段必须增加公开、可测试的 `reportOptions` 适配；
3. 如果领星转发接口不接受 `SKU`，只允许在受控验证后明确降级到 `CHILD`；
4. 缺少日期、SKU/子 ASIN、Sessions 或 Page Views 时必须提示缺失，不能填造零值。

报告按 7 至 30 天窗口创建，保存请求指纹并轮询；相同已完成任务不得重复创建。最近 3 天可重拉以吸收迟到修正。

## 6. 广告

先读取 `api.ads.AdProfiles`，建立 `sid + profile_id` 映射。未授权必须显示“未授权/无数据”，不能当作全部指标为零。

SP 商品日报已确认包含：

- `report_date`；
- `profile_id`、Campaign、Ad Group、Ad ID；
- `asin`、`msku`；
- `impressions`、`clicks`、`cost`、`orders`、`sales`。

SB 和 SD 使用各自日报接口。店铺汇总可先用 Campaign 日报；SKU 归因需要分别验证 SB 创意和 SD 商品报告。

广告按单日请求，持续读取 `next_token`。默认重拉最近 14 天；这是可配置工程默认值，真实账号对账后可调整。

## 7. FBA 库存

主快照使用 `api.warehouse.FbaInventory(sids)`：

- 可售：`afn_fulfillable_qty`；
- 不可售：`afn_unsellable_qty`；
- 预留：processing、transfers、customer order；
- 在途：working、shipped、receiving。

规范化公式：

```text
reserved = processing + transfers + customer_order
inbound = working + shipped + receiving
on_hand = fulfillable + unsellable + reserved
total_with_inbound = on_hand + inbound
```

`afn_actual_shipped_qty` 暂时只作审计字段。在证明它不与 `afn_inbound_shipped_qty` 重叠前，不加入总库存。

每次同步写不可变的“日期 + 店铺 + SKU”快照。跨日期库存绝不能相加后再写回每天。

## 8. 月度计划

SDK 未发现经营计划、销售计划或广告预算的已确认读取方法：

- 不猜测私人接口；
- 不抓取领星网页；
- 不用浏览器自动化绕过开放平台；
- 继续使用本地月度计划文件；
- 只有官方文档或受控返回明确确认后才增加同步。

## 9. 本地状态和失败保护

每个数据集分别保存：

- `status`：idle、syncing、success、partial、failed；
- `last_attempt_at`、`last_success_at`；
- 最近成功日期范围和分页/任务检查点；
- 脱敏错误分类；
- 数据粒度与缺失字段提示。

写入必须使用临时结果/事务、完整性校验和原子替换。单个数据集失败时保留上次成功数据，其他数据集可继续，页面显示过期或缺失；日志不得包含凭据、认证代理 URL 或带签名的下载参数。

## 10. 开发门禁

当前契约阶段只提交文档、静态契约和合成测试，不接真实账号、不改版本号、不改固定出口、DPAPI、连接包、Windows 安装器或浏览器插件。

下一实现阶段必须增加：

1. offset 和 next-token 分页器；
2. 异步报告状态机和安全下载器；
3. `reportOptions` 适配；
4. 本地检查点与原子提交；
5. 合成响应字段、迟到修正和幂等测试；
6. 固定出口网络门禁；
7. 脱敏日志测试。

自动测试通过后才需要一次公司电脑“只读字段探测”。探测只汇报授权状态、字段名、数据条数和日期范围，不展示凭据、代理信息、订单号或业务明细。
