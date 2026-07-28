# 领星同步基础设施设计

## 当前状态

本模块是 `0.6.0` 规划阶段的安全底座，建立在已验收的 Daily Business Agent `0.5.4` 和 PR #7 数据契约之上。

当前代码：

- 不读取 AppID/AppSecret；
- 不创建真实领星连接；
- 不加入 Agent 页面或后台自动任务；
- 不改变现有店铺列表同步；
- 不修改版本号或 Windows 安装行为。

只有合成测试全部通过并完成公司电脑只读字段探测后，才允许把它接入真实同步流程。

## 分页器

### offset + length

`collect_offset_pages` 使用真实返回行数推进 offset，并检查：

- `response_count` 必须等于当前数据行数；
- `total_count` 不能为负；
- 已累计行数不能超过 `total_count`；
- 在尚未达到总数时返回空页视为异常；
- 达到最大页数时停止并报错。

这样可以防止接口返回异常时产生死循环或静默缺页。

### next token

`collect_next_token_pages` 检查：

- 下一游标为空时正常结束；
- 有下一游标却没有数据时失败；
- 游标重复时失败；
- 达到最大页数时失败。

## Sessions/PV 报告请求

`SalesTrafficReportRequest` 固定：

- 报告类型 `GET_SALES_AND_TRAFFIC_REPORT`；
- `dateGranularity=DAY`；
- `asinGranularity=SKU`；
- 请求窗口不超过 30 天；
- 时间必须带 UTC 偏移；
- seller、marketplace 和 region 必须明确。

当前 SDK 2.1.7 没有暴露 `reportOptions`。适配器允许显式选择 `report_options` 或 `reportOptions`，但不会自动尝试另一种写法。具体传输字段必须由公司电脑只读探测确认，避免一次探测同时发出两个真实任务。

请求仍调用 SDK 的签名方法，因此后续接入时继续复用现有 token、签名和固定出口通道，不自行重写鉴权逻辑。

## 异步报告任务

`poll_report_task` 把状态分成：

- 等待/处理中；
- 成功；
- 失败、取消或过期；
- 未知状态。

成功结果中的签名下载 URL 只保存在 `ReportDownloadTicket.download_url` 的瞬时字段中：

- 对象 `repr` 不显示 URL；
- `safe_metadata()` 不包含 URL；
- 本地检查点不得写入 URL；
- 错误日志必须先脱敏。

## 本地数据集存储

数据根目录规划为：

```text
<Agent 数据目录>/lingxing/business_data/<dataset>/
  current.json
  sync_status.json
  generations/
    <generation>/
      rows.json
      metadata.json
```

提交步骤：

1. 验证数据集名称、必填字段、身份字段和敏感字段；
2. 在新的 generation 目录写完整 rows 和 metadata；
3. `fsync` 并完成哈希、行数和元数据；
4. 最后原子替换 `current.json`；
5. 再更新可展示的同步状态。

如果步骤 1 至 3 失败，旧 `current.json` 不变；如果进程在激活前退出，旧成功数据仍可读取。旧 generation 暂不自动删除，避免在尚未建立保留策略前误删数据。

## upsert 和迟到修正

每个数据集使用契约中的稳定身份字段：

- 订单：`sid + amazon_order_id + msku + asin`；
- 广告：日期、sid、profile、广告实体和商品身份；
- 库存：`snapshot_date + sid + msku + asin + fnsku`。

相同身份的新行覆盖旧行，因此近期订单状态、金额和广告归因修正不会重复累计。不同库存日期拥有不同身份，不能相加后复制到每一天。

## 敏感信息边界

数据行和检查点拒绝包含常见敏感字段，包括：

- AppID/AppSecret；
- access/refresh token；
- 认证代理 URL；
- 中继密码和证书指纹；
- 签名参数和下载 URL。

失败消息会处理 Bearer token、URL 用户名密码、token/signature 查询参数，并限制最大长度。同步失败只更新 `sync_status.json`，不会清空或替换成功 generation。

## 下一步门禁

下一阶段是“只读字段探测器”，仍需先自动完成：

1. 只读探测命令和本地结果摘要；
2. 禁止输出业务明细和敏感值；
3. 每个接口只请求最小范围和最小页；
4. 不写入正式经营数据集；
5. 探测失败保留现有店铺缓存和 Agent 功能；
6. Windows 安装包与冻结运行环境测试。

自动测试通过后，才会要求公司电脑执行一次探测。探测结果只允许包含接口授权状态、字段名、数据条数、日期范围和脱敏错误分类。
