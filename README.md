# Daily Business Agent

Daily Business Agent（每日经营数据本地助手）是一款 Windows 本地优先工具，用于在用户电脑上读取经营报表、生成 Excel 和无外部资源依赖的经营看板，并通过企业管理员提供的单文件 `.dba` 安全连接领星开放平台。

> 当前状态：`0.4.0` 统一基线仍是 Draft 候选。它合并经营分析主体与已验收的 0.3.1 单文件连接能力，尚未发布正式 GitHub Release。

## 当前能力

- 读取产品映射、业务、广告、ERP、库存和计划文件；
- 使用店铺、站点和商品复合身份，避免不同店铺之间交叉合并；
- 在本机任务目录生成 Excel、JSON 和本地 HTML 经营看板；
- 同时提供经营分析页面 `/` 与领星连接页面 `/lingxing`；
- 仅监听 `127.0.0.1:8766`；
- 支持双击 `.dba`，或拖到“导入每日经营连接包”桌面快捷方式；
- 在本机校验连接包完整性，隐藏代理密码、证书指纹和内部技术字段；
- AppID、AppSecret 和固定出口凭据使用 Windows 当前用户 DPAPI 加密；
- 真实连接成功后自动删除原始 `.dba`，文件被替换或修改时拒绝误删；
- 获取并缓存领星店铺列表，失败时保留上次成功结果；
- 生成的看板不引用 CDN、远程脚本或外部图片。

## Windows 升级兼容

统一候选继续使用已验收的历史位置：

- 应用目录：`%LOCALAPPDATA%\CTJFyrdian\DailyBusinessAgentApp`
- 数据目录：`%LOCALAPPDATA%\CTJFyrdian\DailyBusinessAgent`

升级会清理旧 PyInstaller `_internal` 运行目录，但不会删除 DPAPI 凭据、店铺缓存、经营任务或生成结果。

## 本地安全边界

- 服务只允许回环地址；
- 原始报表和生成结果保存在用户电脑；
- 公开仓库不包含真实固定出口地址、代理凭据、证书、指纹、AppID、AppSecret、私人连接包或客户数据；
- 上传文件和任务产物被限制在各自工作目录；
- 状态和错误会移除秘密与认证代理 URL；
- Excel 输出防护公式注入文本；
- `.dba` 内部 SHA256 用于发现损坏或篡改，不替代可信交付渠道。

## 从源码运行

建议使用 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-agent.txt
python -m agent.run_agent
```

## Windows 安装包

Windows CI 根据 `VERSION` 构建候选安装程序，并验证：

- 完整测试与公开边界扫描；
- PyInstaller 冻结后的领星 SDK 真实导入；
- 安装、回环监听和两个本地页面；
- `.dba` 文件关联与桌面导入入口；
- 停止 Agent 后原地升级及旧 `_internal` 清理；
- 用户数据保留和卸载边界。

在正式 Release 发布前，请不要把 Actions 候选产物当作稳定版本分发。

## 连接包

`.dba` 由企业管理员在仓库外安全生成和传输。用户双击后，只需在本机输入领星 AppID 和 AppSecret；连接成功后原始文件会自动删除。

## 后续范围

本统一基线暂不接入领星销售、广告、库存和销售计划 API。后续将把这些数据映射到现有经营分析内部结构，并保留手工报表作为对账和故障恢复入口。

## 文档

- [Windows 安装指南](docs/WINDOWS_INSTALL.md)
- [领星连接说明](docs/LINGXING.md)
- [隐私说明](PRIVACY.md)
- [安全设计](docs/SECURITY_DESIGN.md)
- [发布流程](docs/RELEASE_PROCESS.md)
- [第三方依赖许可证](THIRD_PARTY_LICENSES.md)

## License

项目源码采用 [Apache License 2.0](LICENSE)。第三方组件遵循各自许可证。
