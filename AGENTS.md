# Caress

## 边界
- Python 3.15、React/TypeScript、Tailwind CSS 4、Base UI、Bun、SQLite；版本为 2.2.1。只维护 main，可打匹配版本的 Git Tag，不创建 GitHub Release。
- Emby 独占媒体、元数据、人物与图片；Caress 只做 JAV 数据获取、翻译、匹配、封面裁剪和 REST 写入。不得挂载或修改媒体源，不写 NFO，不引入独立媒体库、编辑器或账户体系。
- SQLite 仅保存设置、处理记录、当前批次和来源冷却；不提供历史配置或数据库导入、转换、升级逻辑。
- 按视频/STRM 文件名主干识别，仅剥离末尾 `-C`、`-U`、`-UC`、`-cdN`，不依赖父目录，不模糊匹配或重命名。
- 每个关联 Item 独立验证所选库归属并去重；写前复核服务器、Item 身份和归属。完整读、有限合并、回读验证；保留非受管字段、人物和图片，不主动改变 LockData/LockedFields。重建时整个 Backdrop 集合属于受管范围。
- scrape 按 Emby 当前缺失内容补齐，不覆盖已有有效字段和 Primary；force 经确认后以本次来源结果重建受管字段，缺失值清空；重新准备封面、整个 Backdrop 集合及演员头像，准备失败保留旧图并如实报告，保留其他人物和非受管属性。历史成功不阻止后续补齐；无变更按跳过处理并保留原记录。取消/重启如实报告写入边界，不声称外部回滚。手动裁剪只更新 Primary，不改变刮削记录。
- 保留顺序命中停止、精确身份匹配、来源独占与限速、翻译演员保护、头像有限重试及非致命失败、图片资源验证、Item 互斥和 SQLite 事务。
- 连续批次使用开始时的全局配置快照，追加请求只指定自己的模式和来源；同目标、模式、来源的未完成请求去重。取消涵盖当前及全部等待项，重启不自动续写。两页共用进度，不引入历史任务中心。
- 用户界面固定文案使用中文，内部日志使用英文；来源和可信 Emby 的上游 JSON 错误保留原始内容，不记录请求头或密钥。

## 结构
- 番号规范化只统一已确认的拼写（含 FC2 别名）；查询转换仅服务于搜索请求。身份比较严格保留数字番号的下划线与连字符差异。来源适配器只能根据明确的命名空间证据还原完整番号，再统一验证；离线采集复用同一来源编号恢复函数。AVSOX 从公开搜索、详情 JSON 读取编号和 `from`，核对作品 ID 后与详情页编号交叉验证。
- `emby.py` 为 REST/回读，`library.py` 为只读投影，`identifiers.py` 为文件名；`scraper.py` 顺序调度，`sources/` 独立适配来源。
- 番号分类先识别 FC2 和纯数字格式，其余精确查询 `data/catalog_rules.json` 静态前缀白名单；未命中或分类冲突保持 UNKNOWN，四类来源顺序分别生效。分类只决定查询优先级，不证明作品身份。建表仅使用 JavDB 有码及 AVSOX 无码记录，手动运行 `scripts/catalog.py` 采集并构建，不维护独立历史证据库。HEYZO、HEYDOUGA 等其余分类统一由数据表决定。正式名单变更且核验通过后才更新 `updated_at`；临时建表不自动推进日期。不默认推断有码，不动态学习或在线更新；前缀表不参与来源身份或 DMM CID 验证。
- Gfriends Filetree 是演员别名、标准名称与头像地址的唯一数据源；标准名称用于 Emby 演员显示，未命中保留来源原名。
- `processing.py` 编排加工，`translation.py` 翻译，`portraits.py` 临时头像，`images.py` 验证/YuNet/裁剪；`storage.py` 管原子文件写入，`runtime.py` 管 SQLite，`tasks.py` 管批次，`service.py` 管生命周期与裁剪会话，`api.py` 管 HTTP。
- `sources/javdb.py` 转交 JavDB 网页请求，`javdb_web.py` 解析搜索和作品页；`uncensored.py` 适配一本道、Caribbeancom 和 HEYZO 官方来源。
- Web `pages/` 为作品/设置，`components/` 为共享控件，`hooks/` 为共享状态，`lib/` 为类型与传输。只有表格，保留分页、列表导航、系统主题、键盘访问和现有 Base UI 交互。作品列表使用刷新图标；详情实时读取，裁剪优先使用 Emby 背景并在需要时自动获取原图。
- 安全与数据流细节以 `docs/architecture.md` 为准。修复确有问题的实现，不增加缓存框架、兼容层、ORM 或通用任务框架。

## 开发与交付
- 用 `docker-compose.yml` 构建、部署、重启；Docker 自动创建命名卷，Caress 以非 root 运行，数据持久化于 `/data`，无需挂载 Emby 媒体目录。
- CI 保留 Python 与前端两个 Job；发布前在本地通过 Compose 验证构建、启动和重启。
- 后端：`uv run --no-project --python 3.15 --with-requirements requirements-dev.txt ruff check --no-cache caress tests scripts`；同一环境运行 `python -m pytest tests -q`。
- Python 测试位于 `tests/`，Playwright 测试位于 `tests/web/`，配置保留在 `caress/web/`。前端在仓库外保留此相对目录结构的副本执行 `bun install --frozen-lockfile`、`bun run build`（含严格类型检查）及 `bun run test`；Playwright 需安装 Chromium。
- 测试源、bun.lock、YuNet 模型、许可和正式 Compose 配置必须跟踪 Git；依赖环境、缓存、构建产物、测试输出和临时夹具放仓库外。真实密钥、数据库、日志、图片缓存和本地 Compose 覆盖配置不得提交。
- 保留透明 Logo `assets/logo.png`（512px）和 `caress/web/assets/favicon.png`（64px）的原比例。注释只解释必要的契约、安全边界与算法取舍。
- 真实 Emby 验证优先只读；写入只针对明确隔离的测试 Item。任何清理不得触及媒体源或其他应用资源；未执行的验证不得宣称通过。
