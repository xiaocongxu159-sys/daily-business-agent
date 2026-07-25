# Daily Business Agent

Daily Business Agent（每日经营数据本地助手）是一款面向 Windows 的本地优先工具，用于读取用户主动选择的经营报表，在本机生成 Excel 和无外部资源依赖的经营看板。

> 当前状态：公开源码和 Windows 构建脚本正在 Draft PR 中接受验证。CI 已能生成候选安装包，但尚未发布 GitHub Release，也尚未完成真实 Windows 桌面使用体验验收。

## 主要能力

- 读取用户选择的产品映射、业务、广告、ERP、库存和计划文件；
- 使用店铺、站点和商品复合身份，避免不同店铺之间交叉合并；
- 在任务工作目录内生成 Excel、JSON 和本地 HTML 看板；
- 提供只监听回环地址的本地网页；
- 可选连接领星开放平台，凭据使用 Windows 当前用户 DPAPI 加密保存；
- 生成的看板不引用 CDN、远程脚本或外部图片。

## 本地安全边界

- 服务默认只监听 `127.0.0.1:8766`；
- Agent Token 保存在本机数据目录，不通过正式命令行参数打印；
- 上传文件和任务产物必须位于各自工作目录内；
- 状态和错误信息会移除 AppID、AppSecret、代理地址和 Token；
- Excel 输出会防护以 `=`, `+`, `-`, `@` 开头的公式注入文本；
- 公开仓库不包含固定出口服务器部署文件、生产地址、证书、指纹、代理凭据、客户数据或私人 Git 历史。

详见 [隐私说明](PRIVACY.md) 和 [安全设计](docs/SECURITY_DESIGN.md)。

## 从源码运行

建议使用 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-agent.txt
python -m agent.run_agent
```

浏览器会打开本机领星页面。经营报表页面位于 `http://127.0.0.1:8766/`。

## Windows 安装包

Windows CI 会构建 `DailyBusinessAgent-Setup-0.1.0.exe`，执行漏洞审计、静默安装、登录自启动、本地页面、回环监听、卸载和用户数据保留检查。

在正式 Release 发布前，请不要把 Actions 候选产物当作稳定版本分发。安装与卸载说明见 [Windows 安装指南](docs/WINDOWS_INSTALL.md)。

## 领星连接

领星功能默认不包含任何真实服务器、账号、证书或指纹。用户需要在本机页面中提供自己的 AppID、AppSecret 和经过证书指纹固定的 TLS 出口地址。详见 [领星连接说明](docs/LINGXING.md)。

## 自动验证

公开 PR 使用三条独立验证链：

1. **Public Security Core**：编译、私人标识扫描、接口和全量合成测试；
2. **Browser UX**：真实 Chromium 中的报表、看板和领星操作；
3. **Windows Installer**：依赖一致性、漏洞审计、安装包构建、静默安装和卸载。

所有测试数据均为合成数据。

## 文档

- [Windows 安装指南](docs/WINDOWS_INSTALL.md)
- [领星连接说明](docs/LINGXING.md)
- [隐私说明](PRIVACY.md)
- [安全设计](docs/SECURITY_DESIGN.md)
- [发布流程](docs/RELEASE_PROCESS.md)
- [第三方依赖许可证](THIRD_PARTY_LICENSES.md)
- [安全问题报告](SECURITY.md)
- [贡献指南](CONTRIBUTING.md)

## License

项目源码采用 [Apache License 2.0](LICENSE)。第三方组件继续遵循各自许可证，详见 [NOTICE](NOTICE) 和 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
