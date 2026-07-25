# Third-Party Licenses

Daily Business Agent 源码采用 Apache License 2.0。项目依赖的第三方软件继续遵循各自许可证；本文件不改变或替代这些许可证条款。

每次 Windows 候选构建都会使用 `pip-licenses` 生成带版本、许可证和项目地址的完整环境报告，并与候选安装包一起作为 Actions Artifact 上传。该自动报告包含运行时、构建和测试工具，因此比最终 EXE 实际导入的模块更宽。

## 直接运行时依赖

| 组件 | 当前候选版本/范围 | 许可证标识 |
|---|---:|---|
| pandas | 2.2.3 | BSD |
| NumPy | 2.3.5 | BSD |
| openpyxl | 3.1.5 | MIT |
| FastAPI | 0.139.2 | MIT |
| Starlette | 1.3.1 | BSD-3-Clause |
| Uvicorn | 0.48.0 | BSD-3-Clause |
| Pydantic | 2.13.4 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| HTTPX | 0.28.1 | BSD-3-Clause |
| LingXingApi | 2.1.7 | MIT |
| aiohttp-socks | 0.11.0 | Apache-2.0 |

## 主要传递依赖

候选环境还包含由上述组件引入的依赖，包括但不限于：

| 组件 | 许可证标识 |
|---|---|
| aiohttp | Apache-2.0 AND MIT |
| python-socks | Apache-2.0 |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| asyncssh | EPL-2.0 OR GPL-2.0-or-later |
| certifi | MPL-2.0 |
| orjson | MPL-2.0 AND (Apache-2.0 OR MIT) |
| anyio | MIT |
| h11 | MIT |
| click | BSD-3-Clause |
| python-dateutil | Apache-2.0 / BSD |
| pytz | MIT |
| tzdata | Apache-2.0 |

传递依赖会随固定依赖解析结果变化，以对应候选 Artifact 中自动生成的报告为准。

## 构建与测试工具

| 组件 | 当前候选版本 | 许可证标识 |
|---|---:|---|
| PyInstaller | 6.21.0 | GPL-2.0（带 PyInstaller Bootloader Exception） |
| pyinstaller-hooks-contrib | 2026.6 | Apache-2.0 / GPL-2.0 |
| pytest | 9.1.1 | MIT |
| pip-audit | 2.10.1 | Apache-2.0 |
| pip-licenses | 5.5.5 | MIT |

PyInstaller 的许可证和 Bootloader Exception 适用于其构建与分发方式。项目本身仍按根目录 `LICENSE` 中的 Apache License 2.0 发布；这不改变被打包第三方组件各自的许可证义务。

## Release 检查

正式 Release 前必须：

1. 重新生成候选环境许可证报告；
2. 检查未知、缺失或发生变化的许可证；
3. 保留 Apache、BSD、MIT、MPL、EPL/GPL 可选条款和其他适用许可证要求；
4. 将许可证报告与安装包和 SHA256 一起提供；
5. 不把本文件当作法律意见，必要时由发布者进行专业合规审查。

第三方项目地址和精确版本可在候选构建生成的 `THIRD_PARTY_LICENSES.generated.md` 中查看。
