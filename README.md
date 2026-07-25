# Daily Business Agent

Windows 本地领星同步 Agent。当前候选版本通过私人连接包接入固定出口，中继只转发经过认证的 HTTPS CONNECT；领星 API HTTPS 仍保持端到端加密。

## 当前能力

- 仅监听 `127.0.0.1:8766`；
- 选择 `.dba-connection.json` 和同名 `.sha256` 文件导入固定出口；
- 严格校验连接包结构、SHA256、认证字段和 TLS 证书指纹；
- AppID、AppSecret 和固定出口凭据使用 Windows Current User DPAPI 加密；
- 获取并缓存领星店铺列表，失败时保留上次成功数据；
- Windows 登录后可自动启动并定期同步。

## 仓库边界

本公开仓库不包含：

- 固定出口服务器部署代码；
- 真实中继域名、IP、用户名、密码或证书；
- 私人连接包；
- 领星 AppID、AppSecret 或业务数据；
- 生产数据库、容器、Squid/stunnel 配置。

连接包由企业管理员在仓库外安全生成和传输。导入成功后应删除原始连接包和校验文件。

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

候选安装包会生成在 `release/`，并附带 `.sha256` 文件。

## 安全

请勿在公开 Issue、日志或截图中提交连接包、AppSecret、代理凭据或证书指纹。安全问题请参阅 [SECURITY.md](SECURITY.md)。
