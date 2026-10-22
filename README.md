# Community Booking · 社区设施预约与核验

一个采用 [MIT 许可证](LICENSE) 的 FastAPI / SQLite 社区设施预约与现场核验示例。支持共享容量、预约管理凭证和工作人员核验。**当前运行方式仅支持本机合成演示，不是可直接投产的发行版。**

适合学习小型预约系统的事务、权限和凭证设计，或作为独立场所系统的开发起点。没有社区采用规模或生产可靠性的承诺。

## 已实现

- 配置设施、共享资源、容量、开放时间、预约时长、取消时限、时区及页面名称。
- 按时间片检查共享容量，事务内预约；取消立即释放容量。
- 独立随机保管凭证用于查看和取消；不收集手机号，不提供手机号找回。
- HMAC 签名二维码只用于展示、核验，不具备取消权限；取消、超时或管理员作废后失效。
- 管理员 / 核验员登录、服务器端会话、分批到场、幂等去重、儿童监护确认。
- 管理员处理取消申请、纠正核验、调整资源容量和查看审计记录。
- SQLite 一致性备份与新文件恢复校验；不会用旧备份覆盖运行数据库。

不包含积分、商城、搭子、短信、支付、企业微信、生产部署或真实客户数据。通知集成留待后续独立设计。

## 快速开始

本次实际验证环境为 macOS / Python 3.14.2；其他 Python 版本和操作系统尚未验证。

获取源码后，在项目根目录创建独立虚拟环境：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --index-url https://pypi.org/simple -r requirements-lock.txt
.venv/bin/python -m community_booking.demo --seed
```

访问 <http://127.0.0.1:8765>，不要用 `localhost` 替代该地址。应用严格检查配置的 Host 和 Origin。

启动器固定监听 `127.0.0.1`，没有公网绑定参数。若沙箱禁止端口监听，需要通过执行环境的正常审批流程；不要修改防火墙或安全设置绕过限制。

第一次启动会在项目目录建立权限受限的 `runtime/`：

| 文件 | 用途 | 可以发布吗 |
|---|---|---|
| `config.json` | 从配置示例初始化的本地配置 | 不直接发布，使用根目录配置示例 |
| `demo-identity.json` | 随机签名密钥、合成账户密码哈希 | 不可以 |
| `demo-access.txt` | 本机演示账户的随机密码 | 不可以 |
| `demo.sqlite3` 及 WAL/SHM | 仅本候选版的合成数据库 | 不可以 |
| `synthetic-receipts.json` | 三个合成预约及其保管凭证 | 不可以 |

`--seed` 只允许初始化空数据库；种子收据已存在时不会重复插入。最早一个合成预约接近初始化时间，稍后打开时可能已经过期。此时在页面重新创建合成预约即可，不要修改系统时间。

## 演示流程

1. 在“预约设施”中选设施、日期、时段和人数；填写“合成访客 A”之类的称呼，阅读演示提示后提交。
2. 下载私有保管凭证。页面只在本次内存中保存密钥；关闭页面后不能靠手机号找回。下载文件不要分享。
3. “管理预约”需要预约编号和保管密钥；取消时限内可以自行取消，过晚则提交管理员处理申请，申请本身不释放容量。
4. “工作人员”使用 `runtime/demo-access.txt` 中的合成账户。可通过“从本机演示文件填入账户”选择角色并读取该文件，文件只在页面内读取，登录时只提交选定账户。管理员可以管理当日预约，核验员只能核对与登记。
5. 复制核验凭证，在工作人员页粘贴并核对，再输入本次到场人数。只有预约开始前配置的提前窗口至结束前允许核验。重复同一请求不会重复加人。
6. 再次分批到场时点击“开始下一批”，核对人数后开始新请求。误操作可由管理员凭核验记录编号填写原因撤销；原记录保留。

二维码链接包含 `127.0.0.1`，只在运行演示的电脑有效。另一台手机扫码不会连接到这台电脑。候选版提供手工粘贴核验，不启用摄像头；跨设备扫码不是本次验证范围。

## 配置

编辑 `runtime/config.json` 后重启。

- 示例为全天开放，便于任何时间演示；实际场所应设置开放时段，如 `open_minute: 540`、`close_minute: 1260`。
- 两种设施可引用相同 `resource`，容量按共享资源累加，不按设施分别计算。
- 亲子设施默认容量计算参与儿童，监护人现场另外确认；真实场所必须确定是否将监护人计入物理容量。
- 改动资源 ID、容量口径或协议后不能假定已有预约仍兼容。候选版没有通用迁移管理工具；重构配置应在空的独立演示实例验证。
- 时区可配置；夏令时切换、跨时区实际运营尚未验证。

## 测试与备份

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check community_booking/static/app.js
.venv/bin/python -m community_booking.backup --restore-check
```

测试在系统临时目录建立全新数据库，并在结束后清理。不会访问远程服务。Node 只用于可选的 JS 语法检查，应用运行不需要 Node。

更多说明见 [SECURITY.md](SECURITY.md)、[DEPLOYMENT.md](DEPLOYMENT.md)、[BACKUP.md](BACKUP.md)、[TEST-RESULTS.md](TEST-RESULTS.md)。

## 源码发布集合

`RELEASE_ALLOWLIST.txt` 是明确文件清单。使用 `tools/check_release.py` 检查，使用 `--stage` 创建新的干净本地集合；已存在的目标不会被覆盖。

```sh
.venv/bin/python tools/check_release.py
.venv/bin/python tools/check_release.py --stage ../community-booking-dist
```

不要整目录压缩当前工作副本。`runtime/`、`.venv/`、缓存、数据库和临时材料均不属于发布集合。发布清单只包含源码、合成配置示例、测试与文档。

## 许可与贡献

本项目采用 [MIT 许可证](LICENSE)，Copyright (c) 2026 Xu Haoyuan。依赖继续适用各自的许可证，参见 [PROVENANCE.md](PROVENANCE.md)。

欢迎提交可复现的问题或小范围改进。请使用合成示例，不要在 Issue、PR 或附件中提交真实预约、凭证、账户文件或其他个人信息。提交修改前运行回归测试，并同步更新文档与发布文件清单。

直接和间接依赖的已安装版本及其许可证文本保存在 [THIRD-PARTY.md](THIRD-PARTY.md) 和 `third_party_licenses/`。页面无第三方图片、字体、CDN 或跟踪代码。

## English summary

Community Booking is an MIT-licensed FastAPI / SQLite example for community-facility reservations and staff check-in. It demonstrates shared capacity, transactional booking, role-based staff access and signed QR tickets. A separate random management capability is required to view or cancel a booking; phone-only recovery is not supported.

The supported launcher binds to loopback only and creates synthetic demo accounts and independent local secrets. It needs no SMS, external API or production credentials. Follow the quick-start commands above using Python 3.14 (tested on macOS). The UI and detailed documentation are in Chinese. Public hosting, cross-device scanning, large-scale operation and production identity management are outside the current implementation. Never publish the runtime directory; use the explicit release allowlist.
