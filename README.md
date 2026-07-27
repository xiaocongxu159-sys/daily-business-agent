# Daily Business Agent

Windows 本地领星同步 Agent。当前候选版本通过单个私人 `.dba` 连接包接入固定出口，中继只转发经过认证的 HTTPS CONNECT；领星 API HTTPS 仍保持端到端加密。

## 当前能力

- 仅监听 `127.0.0.1:8766`；
- 支持双击 `.dba` 文件，或把文件拖到桌面的“导入每日经营连接包”快捷方式；
- `.dba` 内部封装完整性校验，普通用户不需要处理单独的 `.sha256` 文件或技术字段；
- 严格校验连接包结构、内部完整性、认证字段和 TLS 证书指纹；
- 真实连接验证成功后，AppID、AppSecret 和固定出口凭据使用 Windows Current User DPAPI 加密保存；
- 保存成功后由本机 Agent 自动删除原始 `.dba` 文件；文件被替换或发生变化时拒绝自动删除；
- 获取并缓存领星店铺列表，失败时保留上次成功数据；
- Windows 登录后可自动启动并定期同步。

## 仓库边界

本公开仓库不包含：

- 固定出口服务器部署代码；
- 真实中继域名、IP、用户名、密码或证书；
- 私人连接包；
- 领星 AppID、AppSecret 或业务数据；
- 生产数据库、容器、Squid/stunnel 配置。

`.dba` 由企业管理员在仓库外安全生成和传输。内部 SHA256 用于发现文件损坏或内容被改动，不代替可信传输渠道；应通过 SSH、受控存储或其他可信方式交付。

## 用户导入流程

1. 安装 Windows Agent，并保留“导入每日经营连接包”桌面快捷方式；
2. 双击管理员提供的 `.dba`，或把它拖到该快捷方式；
3. 在本机页面输入领星 AppID 和 AppSecret；
4. Agent 校验连接包、测试固定出口并使用 DPAPI 加密保存；
5. 成功后 Agent 自动删除原始 `.dba`，页面不展示代理密码、完整证书指纹或内部校验值。

## 本地开发

```powershell
python -m pip install -r requirements-test.lock
python -m unittest discover -s tests -p "test_*.py"
python -m agent.run_agent
```

## 构建 Windows 安装包

需要 Python 3.12、PyInstaller 和 Inno Setup：

```powershell
.\packaging\build_installer.ps1
```

候选安装包会生成在 `release/`，安装程序本身附带公开发布用 `.sha256` 文件。这个安装包校验文件与私人 `.dba` 的内部完整性校验是两件不同的事。

## 安全

请勿在公开 Issue、日志或截图中提交 `.dba`、AppSecret、代理凭据或证书指纹。安全问题请参阅 [SECURITY.md](SECURITY.md)。
