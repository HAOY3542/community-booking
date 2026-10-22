# 第三方依赖声明

本项目没有打包依赖源码或虚拟环境。以下说明依据本机已安装发行包的元数据与 LICENSE 文件，不声称已完成独立法律审查或漏洞审计。

| 依赖 | 本次版本 | 用途 / 原始许可 |
|---|---|---|
| FastAPI | 0.116.1 | API 框架；MIT |
| Starlette | 0.47.3 | ASGI 框架；BSD-3-Clause |
| Pydantic / pydantic-core | 2.12.5 / 2.41.5 | 输入验证；MIT |
| Uvicorn | 0.35.0 | 本机 ASGI 服务；BSD-3-Clause |
| qrcode | 8.2 | SVG 二维码生成；BSD，具体以原始文本为准 |
| HTTPX / HTTPCore | 0.28.1 / 1.0.9 | 测试客户端；BSD-3-Clause |
| AnyIO | 4.13.0 | 异步支持；MIT |
| annotated-types | 0.7.0 | 类型辅助；MIT |
| typing-extensions | 4.15.0 | 类型兼容；PSF-2.0 |
| typing-inspection | 0.4.2 | 类型分析；MIT |
| idna | 3.11 | 域名编码；BSD-3-Clause |
| sniffio | 1.3.1 | 异步环境识别；MIT OR Apache-2.0 |
| click | 8.3.1 | Uvicorn CLI；BSD-3-Clause |
| h11 | 0.16.0 | HTTP 协议支持；MIT |
| certifi | 2026.1.4 | 测试 HTTP 客户端的证书依赖；MPL-2.0 |

`third_party_licenses/` 保留上述发行包所附许可证文本，包括 sniffio 的两种许可文本。`requirements-lock.txt` 记录版本快照。未来修改或升级依赖后需同步核对，不要把本清单当作永久完整的 SBOM。

依赖安装入口限定官方 Python 包索引 <https://pypi.org/simple>。应用自身无需任何外部 API、账户或 CDN。Python、SQLite、系统库以及浏览器由运行环境提供，没有复制到待发布集合。
