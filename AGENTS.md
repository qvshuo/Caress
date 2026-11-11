# Caress

## 边界
- Python 3.15、React/TypeScript、Tailwind CSS 4、Base UI、Bun、SQLite；版本为 2.0.1。只维护 main，可打匹配版本的 Git Tag，不创建 GitHub Release。
- Emby 独占媒体、元数据、人物与图片；Caress 只做 JAV 数据获取、翻译、匹配、封面裁剪和 REST 写入。不得挂载或修改媒体源，不写 NFO，不引入独立媒体库、编辑器或账户体系。
- SQLite 仅保存设置、匹配规则、处理记录、当前批次和来源冷却；不提供历史配置或数据库导入、转换、升级逻辑。
- 按视频/STRM 文件名主干识别，仅剥离末尾 `-C`、`-U`、`-UC`、`-cdN`，不依赖父目录，不模糊匹配或重命名。
- 每个关联 Item 独立验证所选库归属并去重；写前复核服务器、Item 身份和归属。完整读、有限合并、回读验证；保留无关字段/人物/图片，不主动改变 LockData/LockedFields。
- scrape 按 Emby 当前缺失内容补齐，不覆盖已有有效字段和 Primary；force 有有效来源值才替换受管字段，并明确确认。历史成功不阻止后续补齐；无变更按跳过处理并保留原记录。取消/重启如实报告写入边界，不声称外部回滚。手动裁剪只更新 Primary，不改变刮削记录。
- 保留顺序命中停止、精确身份匹配、来源独占与限速、翻译演员保护、头像有限重试及非致命失败、图片资源验证、Item 互斥和 SQLite 事务。
- 执行中允许追加请求，每次提交保留模式和来源；同目标、模式、来源的未完成请求去重。取消涵盖当前及全部等待项，重启不自动续写。两页共用进度，不引入历史任务中心。

## 结构
- `emby.py` 为 REST/回读，`library.py` 为只读投影，`identifiers.py` 为文件名；`scraper.py` 顺序调度，`sources/` 独立适配来源。
- `processing.py` 编排加工，`translation.py` 翻译，`portraits.py` 临时头像，`images.py` 验证/YuNet/裁剪；`runtime.py` 管 SQLite，`tasks.py` 管批次，`service.py` 管生命周期与裁剪会话，`api.py` 管 HTTP。
- Web `pages/` 为作品/设置，`components/` 为共享控件，`hooks/` 为共享状态，`lib/` 为类型与传输。只有表格，保留分页、列表导航、系统主题、键盘访问和现有 Base UI 交互。作品列表使用刷新图标；详情实时读取，裁剪优先使用 Emby 背景并在需要时自动获取原图。
- 安全与数据流细节以 `docs/architecture.md` 为准。修复确有问题的实现，不增加缓存框架、兼容层、ORM 或通用任务框架。

## 开发与交付
- 用 Docker Compose 构建、部署、重启；生产只有 Python 运行时，以非 root 运行，仅挂载 `/data`。
- CI 保留 Python 与前端两个 Job；发布前在本地通过 Compose 验证构建、启动和重启。
- 后端：`uv run --no-project --python 3.15 --with-requirements requirements-dev.txt ruff check --no-cache caress tests`；同一环境运行 `python -m pytest tests -q`。
- 前端在仓库外的副本执行 `bun install --frozen-lockfile`、`bun run build`（含严格类型检查）及 `bun run test`；Playwright 需安装 Chromium。
- 测试源、bun.lock、YuNet 模型和许可必须跟踪 Git；依赖环境、缓存、构建产物、测试输出和临时夹具放仓库外。真实密钥、数据库、日志、图片缓存和本地 Compose 不得提交。
- 保留透明 Logo `assets/logo.png`（512px）和 `caress/web/assets/favicon.png`（64px）的原比例。注释只解释必要的契约、安全边界与算法取舍。
- 真实 Emby 验证优先只读；写入只针对明确隔离的测试 Item。任何清理不得触及媒体源或其他应用资源；未执行的验证不得宣称通过。
