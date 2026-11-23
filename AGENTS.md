# Caress

- 以 [架构契约](docs/architecture.md) 为业务边界。修复已确认的问题，保留调用方依赖的行为，不为整理文档、注释或布局重构代码。避免新增框架、依赖、兼容层和无实际用途的抽象。
- 保持 Python 3.15、React/TypeScript、Tailwind CSS 4、Base UI、Bun 和 SQLite。界面文案用中文，内部日志用英文。注释只解释非直观算法、外部协议或设计取舍。
- 只维护 `main`。Python、前端和 Compose 镜像版本同步，可打对应 Git Tag，不建 GitHub Release。用 `docker-compose.yml` 部署，发布前验证构建、启动和重启。
- 后端运行 `uv run --no-project --python 3.15 --with-requirements requirements-dev.txt ruff check --no-cache caress tests scripts`，同一环境运行 `python -m pytest tests -q`。
- 前端在仓库外复制 `caress/web/` 和 `tests/web/`，保持相对路径。在前者运行 `bun install --frozen-lockfile`、`bun run build`（含严格类型检查）和 `bun run test`（需 Chromium）。CI 保留 Python 和前端两个 Job。
- 跟踪测试源、`bun.lock`、YuNet 模型与许可、正式 Compose 配置。依赖、缓存、构建与测试产物放仓库外。密钥、数据库、日志、本地 Compose 覆盖和临时文件不得提交。
- 保留透明 Logo 和 favicon 原比例。真实 Emby 验证优先只读，只写明确隔离的测试 Item。任何清理不得影响媒体源或其他应用资源。如实报告未执行的检查。
