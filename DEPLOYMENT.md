# 仅本机部署示例

本文件不提供公网部署命令。候选版只验证单进程回环服务。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --index-url https://pypi.org/simple -r requirements-lock.txt
.venv/bin/python -m community_booking.demo --init-only --seed
.venv/bin/python -m community_booking.demo --port 8765
```

配置示例是根目录 `config.example.json`。首次启动将其写入自己的私有 `runtime/config.json`。再次启动不会覆盖配置、重新生成密钥或重复播种；运行材料不完整时拒绝启动，避免把丢失的签名密钥静默替换掉。

- 浏览器入口：`http://127.0.0.1:8765`。
- 默认端口可改为其他未占用的本机高位端口。二维码随配置入口生成；改变端口后旧链接不会自动跳转。
- 服务只读自己的源码和配置，读写自己的 `runtime/`。不需要外部服务凭据。
- 停止：在启动终端按 Ctrl+C。重启撤销工作人员会话，但保管凭证和未过期二维码仍可在同一密钥下验证。
- `requirements.txt` 为运行直接依赖，`requirements-test.txt` 加测试依赖；`requirements-lock.txt` 是本次执行环境中相关依赖版本的快照，不是带哈希的供应链锁定文件。
- 2026-11-04 已在 macOS / Python 3.14.2 全新虚拟环境中，从 PyPI 完整安装锁定依赖，并通过 `pip check` 与本地回归。具体范围见 [TEST-RESULTS.md](TEST-RESULTS.md)，仍应使用独立环境，不依赖系统级包。

不要将本机演示通过隧道、反向代理或更改主机绑定暴露出去。未来的对外部署需要单独设计和审阅。
