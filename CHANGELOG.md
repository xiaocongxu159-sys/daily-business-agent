# Changelog

本项目遵循语义化版本号。当前内容尚未发布正式 Release。

## [0.1.0] - Unreleased

### Added

- 本地优先 Windows Agent 和回环网页；
- 产品映射、业务、广告、ERP、库存和计划文件读取；
- 多店铺复合身份与歧义安全合并；
- Excel、JSON 和无外部资源依赖的 HTML 经营看板；
- 路径穿越、上传大小、公式注入和跨站请求防护；
- 可选领星连接、Windows 当前用户 DPAPI 加密和 TLS 指纹固定；
- 真实 Chromium 报表、看板和领星操作验证；
- Windows 安装器构建、静默安装、自启动、回环监听、卸载和数据保留验证；
- 严格依赖漏洞审计与第三方许可证报告。

### Security

- 升级到已通过当前严格审计的 FastAPI、Starlette 和 python-multipart 组合；
- 状态、日志和错误信息移除凭据、Token 和认证代理地址；
- 固定出口服务器配置、证书、私钥和生产凭据不进入公开仓库；
- 测试仅使用合成数据。

### Known limitations

- 尚未完成真实 Windows 桌面人工体验验收；
- 当前候选安装包没有商业代码签名，Windows 可能显示未知发布者或 SmartScreen 提示；
- 公开仓库不提供固定出口服务器部署；
- FastAPI 测试栈和 pandas 当前会产生部分上游弃用/未来行为警告，已记录为后续维护项，但当前安全、浏览器和 Windows 验证均通过；
- 0.1.0 尚未发布，不保证候选 Actions 产物长期保留。
