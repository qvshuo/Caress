# Caress 架构收敛与 Base UI 迁移交付

版本保持 1.6.2。运行时采用 Python 3.15.2、FastAPI 0.162.2、Pydantic 2.14.3、React 19.3.1、TypeScript 7.0.2、Bun 1.4.4、Tailwind CSS 4.3.5、Base UI 1.9.0、Sonner 2.0.8。shadcn/ui 使用官方 Base UI 组件组织与组合方式；核对时 CLI 稳定版为 4.23.0，CLI 不作为生产依赖。

## 目录与模块

```text
.github/workflows/
  checks.yml                 Python、前端、浏览器和 Compose 检查
  release.yml                完整检查通过后发布匹配版本的源代码
assets/logo.png
caress/
  models.py                  Work、MediaFile、来源/最终元数据、图片与选择契约
  service.py                 Application 生命周期、Caress 资源与作品投影
  operations.py              完整表单保存、候选预处理、裁剪、头像和任务选择
  tasks.py                   唯一 TaskQueue：排队、并发、取消、重试、历史
  processing.py              唯一 WorkProcessor 和成熟的单作品阶段实现
  runtime.py                 SQLite、事务、结构校验和提交后的缓存发布
  artwork.py                 图片角色、来源选择、保护、重试输入与指纹
  images.py                  下载结果验证、图片计划、YuNet、无损裁剪与替换
  storage.py                 原子写入与已处理异常的文件回滚
  scraper.py                 来源并发、顺序选择、分类及来源锁
  sources/                   保留全部十个来源及 JavDB API/web 适配器
  actors.py                  验证身份、别名、歧义、映射和演员库投影
  portraits.py               gfriends 索引、头像验证及替换
  translation.py             独立标题/剧情处理、原文与翻译指纹
  emby.py                    精确关联、纯同步计划、写入及回读验证
  settings.py                单一 SQLite 配置与校验
  http.py                    Scrapling、FlareSolverr、会话、限速及冷却
  scanner.py                 只读扫描和严格文件名识别
  api.py                     HTTP 验证、调用、错误和查询投影
  directories.py             挂载内目录浏览
  logging.py                 JSON 事件与任务/作品上下文
  data/                      初始化规则、YuNet 模型及许可证
  web/
    src/pages/               作品、演员、任务和设置
    src/components/ui/       shadcn/ui + Base UI 唯一交互组件边界
    src/components/          表单、列表、确认、图片及裁剪组合
    src/hooks/               必要的页面交互状态
    src/lib/                 API、类型、标签和纯函数
    tests/                   组件和完整页面浏览器回归
    playwright.config.ts
    package.json、bun.lock
    components.json
    build.ts、dev.ts
    assets/favicon.png
    index.html、tsconfig.json、bunfig.toml
  __main__.py、__init__.py
  # web/dist 仅在构建环境和生产镜像中存在
tests/                      pytest，临时 SQLite、临时文件及 HTTP mock
docs/                       设计契约和本交付说明
requirements.txt
requirements-dev.txt
pytest.ini、ruff.toml
Dockerfile、docker-compose.example.yml
README.md、AGENTS.md、LICENSE
```

新增模块为 `service.py`、`operations.py`、`artwork.py`。`processing.py` 保留阶段函数并承载 WorkProcessor；TaskQueue 不再处理翻译、映射、图片及同步。Work/MediaFile 从 scanner 移到 models，来源适配器只改为返回 SourceMetadata，保留解析及网络算法。没有按接口机械创建 Service，也没有引入 ORM、调度框架、认证或前端状态框架。

## 核心模型与状态所有权

- **Work / MediaFile**：标准番号与文件身份。文件名、绝对路径、字幕、破解和分集属性保留；不同文件保持独立媒体 ID 和同步状态。
- **SourceMetadata / ArtworkCandidate**：来源原始字段及图片候选。SourceResult/SourceVariant 保留原始候选、演员 ID 和单独的 processed_fields。
- **WorkMetadata**：唯一最终元数据模型，仅包含 code 和十个可编辑字段，不含来源 URL、分类或图片候选。
- **WorkSelection**：字段来源、人工保护、固定原文、演员映射及处理指纹。人工值不覆盖固定原文。manual_fields 是字段保护的唯一表达。
- **ArtworkState / ArtworkRoleState**：每角色当前文件、选择、候选、内容指纹、来源方式、保护、处理状态及错误。待处理输入固定保存在同一 artwork 记录中，不依赖后来变化的候选。
- **WorkHealth**：当前元数据、待恢复阶段及实际文件问题的查询投影。EmbySyncState 独立保存在每个 media.sync 中；任务结果是执行历史。

业务状态只由对应模块维护：Scanner 发现身份，RuntimeStore 保存当前输出，TaskQueue 维护执行，artwork 管理图片角色，Actors 管理身份，EmbySync 管理同步。前端使用后端状态及进度时间，不根据历史任务推导当前作品状态。

## SQLite 结构与写入入口

使用内置 SQLite + JSON，不建立大量字段表。数据库结构版本为 `PRAGMA user_version=2`；旧结构明确拒绝，不提供迁移或兼容字段。启动检查表列及 JSON 有效性。

| 表 | 关键列 | 数据及所有权 |
| --- | --- | --- |
| records | kind, id, data；联合主键 | metadata：最终 WorkMetadata；scan/catalog：仅扫描身份和警告；result：当前输出的阶段报告；actor：歧义问题；counter：任务序号；cooldown：来源/主机冷却；item_sync、portrait_sync：已验证写入的指纹和受管范围 |
| media | id 主键, code, path 唯一, data, sync | 原文件身份与独立 Emby 关联；扫描更新 data，EmbySync 只更新 sync |
| variants | code, source 联合主键, data | 原始候选与独立处理字段；RuntimeStore.save_variant 验证 SourceVariant |
| selections | code 主键, data | RuntimeStore.save_selection / record_result 验证 WorkSelection；字段与图片选择的唯一存储 |
| tasks | id 主键, data | TaskQueue 的任务头和计数；RuntimeStore.save_task 写入 |
| task_items | task_id 外键, code, position, data；联合主键 | 执行阶段、有限事件及结果；删除任务级联删除执行项，不删除作品输出 |
| options | id=1, data | Settings 的唯一持久化配置；运行批次使用稳定快照 |
| rules | kind 主键, data | CatalogRules / ActorAliases 的验证规则；JSON 仅用于首次初始化 |

事务统一经过持久化入口。嵌套 savepoint 的缓存发布延迟到外层提交完成；SQL 提交失败时不发布扫描、任务或序号投影。任务读取返回分离对象，调用者必须显式保存。进度提交只更新一个执行项；计数根据 SQLite 中的实际状态转换原子增减，不依赖调用者的旧快照。current 指针由已提交的执行项投影，不重复持久化阶段内容。作品进度和最近执行项直接查询已提交投影，不复制整份任务历史；任务分页只复制返回页，搜索一次读取路径投影，避免逐项数据库查询。扫描不再持久化标题、演员、海报和排队状态的第二份副本。

媒体暂时消失时只更新扫描/媒体身份，不删除已保存元数据、候选或人工保护。重启从 SQLite 重建投影；中断任务及执行项标记为可重试失败，同步中的媒体恢复 pending，保留已完成输出和历史。

## 处理阶段、图片与配置

阶段契约见 [architecture.md](architecture.md)。正常流程为 Resolve → Fetch → Select → Transform → Persist → Artwork → Sync。

批次刮削与手工预览共享来源作品锁、限速预算；同一作品的搜索/详情不重复等待。来源并发获取，最终按用户路由顺序选取第一个成功来源，不借用其他来源字段。fill_missing 更新自动值并保留人工保护及显式空值；refresh 按刷新策略替换受管输出。翻译失败保留旧有效值；标题与剧情各自保留输入、指纹和错误。阶段修复直接使用保存的失败输入。演员重映射离线执行；头像任务不翻译或重新刮削作品。

图片角色继续使用原文件约定：`poster` 对应 Poster，`fanart` 对应 Original，`fanart1..100` 对应有序 Fanart。自动和手动选择共享同一角色状态。`samples_protected` 表达整个手选剧照组的保护，包含用户明确排除的索引。自动修复始终重新读取当前保护；刷新才清除保护。图片完整验证和暂存后替换，文件事务包围 SQLite 提交；演员更名复制头像也包围在同一提交边界。此机制处理捕获到的失败，不宣称提供跨文件系统与 SQLite 的断电原子事务。

Application 负责配置保存与服务创建/退出，Caress 负责稳定配置快照及共享资源。新配置保存在 options，在批次结束、取消后的空闲边界或下一批次开始激活。旧 Translator/WebClient 明确关闭后替换。删除 pending_settings 对象、on_idle 回调及递归保存配置；API 的 pending_settings 仅为数据库配置与活动快照的比较结果。

手工保存和同步持有作品锁至完成。图片预览持有操作锁，避免配置切换关闭仍在使用的共享客户端。队列、配置操作锁、作品锁各自拥有明确用途。

## Emby 同步

1. **Resolve**：读取 Movie、MediaSources 和 AdditionalParts，只用完整路径建立关联；缺失或多义路径保持 pending。
2. **Plan**：plan_sync 接收当前输出、Emby 完整项目、已验证同步记录及图片指纹，返回 SyncPlan。生成计划不产生网络、文件或数据库副作用。
3. **Apply**：仅执行需要的元数据更新、图片上传/删除和演员头像更新。保留其他 ProviderIds、非受管人员、标签及不属于受管字段的完整项目数据。
4. **Verify**：元数据回读按字段比较；图片按真实 MIME/base64 上传，再读取槽位和 original 图片并比较像素；删除后确认槽位消失。只在验证成功后持久化阶段指纹。

GenreItems/TagItems 使用真实 4.10 schema。字幕与破解版本汇总到实际关联项目的受管标签，分集不作为标签。Primary 为封面/头像，Backdrop 依次为原始封面和剧照。锁定受管输出。文件指纹与图片解码比较在线程中执行，避免大图阻塞异步服务。

官方参考：[项目更新接口](https://dev.emby.media/reference/RestAPI/ItemUpdateService.html)。另读取真实 Emby 4.10.2.0 的 `/emby/swagger.json`（433 条路径），并执行独立合成媒体的写入/回读；schema 和凭据均不入库到 Git。

## Base UI 迁移报告

采用 [shadcn/ui Base Dialog](https://ui.shadcn.com/docs/components/base/dialog) 及官方 Base UI 实现结构，保留 Caress 的 CVA、颜色、间距、尺寸和主题。没有为迁移新增业务页面或视觉体系。

| 组件 | 原实现 → 新实现 | API 与调用变更 | 行为与验证 |
| --- | --- | --- | --- |
| Button | Radix Slot / 原生 button → Base UI Button | 删除 asChild，采用 render 组合；保留 variant/size 与所有现用尺寸 | 普通点击、禁用、表单提交、作为 Dialog Trigger/Close 的 render 元素通过浏览器回归 |
| Badge | Radix Slot → 语义化 span + CVA | 删除未使用的 asChild；现有页面调用保持 | 展示组件无额外交互状态；类型检查和页面回归通过 |
| Dialog | Radix Dialog → Base UI Dialog | Overlay 内部改 Backdrop，Content 改 Popup；关闭按钮采用 render；描述 asChild 改 render；动画切换到 starting-style/ending-style | 受控/非受控、Escape、焦点约束与恢复、嵌套关闭通过；确认框、目录浏览、图片查看、裁剪统一复用封装 |
| Select | Radix Select → Base UI Select | Root 传 items 标签映射；值处理 null；Portal → Positioner → Popup → List；删除 position=popper 及 Radix CSS 变量，使用 anchor-width/available-height/highlighted | 鼠标选择、键盘 End/Enter、显示标签、弹窗内选择及焦点恢复通过；针对初始焦点时序另执行 20 次重复验证 |
| Checkbox | Radix Checkbox → Base UI Checkbox | checked 改 boolean，部分选中使用独立 indeterminate；非原生根节点显式使用 inline-flex，禁用/选中样式使用 data-disabled / data-checked | 受控/非受控、16px 尺寸、禁用反馈、空格、鼠标和 aria-checked=mixed 通过；列表多选调用同步迁移 |
| Sonner | 分散保存提示布尔值 → 共享 Sonner Toaster | 保存成功发出 toast；Toaster 在入口挂载，系统主题 | 去掉两份 savedNotice 状态；完整表单保存回归通过 |
| Input / Textarea / Table / Card / Alert / Empty | 语义化 HTML + Tailwind | 保留；展示组件不增加 Headless 库 | 严格类型检查、构建、页面及主题回归通过 |

Dropdown Menu、Popover、Tooltip、Radio Group、Tabs、Accordion、Scroll Area 在现有代码中没有 Radix 实现或业务调用，因此没有为它们新增组件。主导航保留语义化 nav/button。

调用位置：Dialog 统一服务于 `confirmation-dialog.tsx`、`directory-browser.tsx`、`image-viewer.tsx`、`crop-editor.tsx`；Select 的 API 适配集中在 `form-controls.tsx`，由 library、actors、tasks、work 页面及 work-list 复用；Checkbox 的三态调用集中在 `list-controls.tsx`。Button/Badge 继续由页面和共享渲染器使用，未保留 Slot 适配。

所有业务页面只依赖 components/ui 或共享组合组件。来源 Select 的重复控件已删除；原文使用独立标签，只有后端明确标记的译文显示翻译标识。未保存表单和裁剪保留在内存；进度轮询更新真实变化，使用后端 progress_at 更新详情，过期响应不会覆盖较新的保存/刷新。

删除直接依赖：

- `@radix-ui/react-dialog` 1.3.1
- `@radix-ui/react-select` 2.4.1
- `@radix-ui/react-checkbox` 1.4.1
- `@radix-ui/react-slot` 1.5.0

bun.lock 中相应 Radix 包已清理；仓库扫描没有 Radix 导入、asChild 或 Radix 专属样式变量。`@base-ui/react` 是唯一通用 Headless UI 依赖；react-image-crop 保留为现有专用图片编辑器。

浏览器验证由 Playwright 驱动真实 Chromium，覆盖浅色/深色；没有把自动测试结果伪称为人工验收签字。另对真实 9308 的桌面/移动尺寸和两种主题进行视觉复核，截图留在仓库外。

## 删除的历史协调与重复表达

- 含图片候选的泛化 Metadata，替换为 SourceMetadata / WorkMetadata；没有保留 Metadata 别名。
- TaskQueue 中的扫描、配置资源、翻译、映射、图片处理和同步编排。
- API Handler 中的完整作品保存事务、候选处理、裁剪和头像业务。
- 独立的 image_choices、manual_image_roles、image_source、pending_images 和重试 protected_roles 持久化状态；请求中的 image_choices 仅为表单命令。
- 根据 field_sources=custom 重复推导人工保护的分支。
- pending_settings 对象、on_idle、apply_pending_settings 和配置递归回调。
- 重复 settings_path、旧 runner_factory 命名及提交前手工修复扫描缓存的分支。
- 扫描结果中的持久化标题/演员/海报/排队副本，以及媒体暂时消失时删除候选和选择的分支。
- 任务缓存对调用者暴露的可变对象、嵌套事务提前发布缓存的路径。
- macOS 资源分叉文件进入 Docker 构建上下文的路径，新增 `._*` 排除；Ruff 显式声明第一方包，保证仓库外及 Linux 检查一致。
- 前端不再存在的“切换元数据目录/运行域”兼容分支、重复保存提示状态、重复原文来源选择器。

保留 source 页面结构变体、官方站点特例和 JavDB API/web fallback：这些仍服务于当前来源协议，不是旧数据库或旧架构兼容层。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| Python 3.15 自动测试 | 本机及一次性 Linux 容器均为 60 项通过；不依赖真实来源或 Emby |
| 大批量进度 | 本机合成基准：1 万执行项、100 次持久化进度更新约 0.010 秒 |
| Ruff | 完整 caress 和 tests 检查通过 |
| TypeScript | 包含业务、构建脚本及测试的严格检查通过 |
| 前端生产构建 | Bun 构建通过 |
| 浏览器完整回归 | 浅色/深色共 16 项；另 Select 键盘焦点重复 20 次通过 |
| Docker Compose | 多阶段镜像构建、9308 全新数据库启动及健康检查通过 |
| 重启 | Compose 重启后作品、输出、任务号和执行记录保留 |
| 镜像边界 | 最终镜像不含 tests、web/tests 或 pytest；生产无 Bun/Node 前端运行时 |
| 只读真实扫描 | 947 个作品，800 个已知有码分类；原媒体不修改 |
| 真实多来源 | JavDB/JavBus/AVbase/AirAV found；DMM region_blocked；按路由选择 JavDB |
| 真实处理 | 元数据、图片、头像成功；最终候选验证 11 个图片角色的候选和指纹，并执行离线演员重映射；取消及新任务重试完成，任务号递增 |
| 真实 Emby 4.10.2.0 | 独立临时库中元数据、taxonomy、人员、ProviderIds、锁定、Primary、两个 Backdrop、头像、背景删除及重复保存通过 |
| Emby 非受管保护 | 临时项目预置的非受管评分、ProviderId、人员与标签保留；原有五个媒体库的名称/ID一致 |
| 9307 | 未部署、未重启；运行起始时间仍为 2026-11-06T18:04:20.913589768Z |
| GitHub Actions | 已配置 PR、main 和 Release 的完整检查依赖；未推送，因此没有宣称远端工作流已执行 |

pytest 覆盖：文件名/STRM/后缀/版本/分集/无效文件/父目录隔离；来源排名与并发、跨客户端来源互斥和作品间限速；人工保护、刷新、原文、翻译指纹与失败保留；演员歧义及离线重映射；图片验证、无损裁剪、完整选择暂存、文件与数据库回滚；实际 SQLite 提交失败、旧快照并发更新、回滚后安全重试、序号、缓存和重启；排队前序输出、十作品上限、取消、重试、配置边界、单作品同步锁；Emby 精确路径、多媒体共用项目、纯计划、失败回读、图片实际内容及幂等性；API 首次配置、完整表单保存和重启。

浏览器覆盖：受控/非受控 Dialog/Select/Checkbox、键盘/鼠标、焦点约束和恢复、嵌套弹窗、三态复选、未保存表单保护、任务轮询、离页确认、图片预览/裁剪及保存时机。

## 部署范围与剩余边界

9308 使用 `/home/anjing/Lab/caress-architecture-check` 下的独立源码与数据，测试服务名为 `caress-check`，避免共享网络 DNS 别名冲突；挂载只读 `/mnt/strm`。原 `caress-9308` 容器及旧数据保留。真实来源测试期间禁用 9308 的 Emby 写入；真实同步只针对本次创建的临时合成媒体库。测试结束清理临时库，确认原有五个媒体库的名称/ID一致。

真实翻译服务验收仍取决于单独授权的测试配置；不读取或复制未获授权的生产翻译密钥。HTTP Mock 已验证兼容接口、串行处理、原文保护、指纹、失败保留和阶段重试。

这次真实来源烟雾测试没有逐一覆盖所有十个站点及所有分类。DMM 当前出口受地区限制；这是已分类并保留的来源结果，不能把其他来源成功写成 DMM 已通过。所有现有适配器保留，自动测试不连接第三方。

文件事务是已处理失败的回滚机制，不是断电恢复日志；磁盘或文件系统本身失效时不能承诺恢复。中断阶段输入和状态持久化，可在重启后显式重试。

示例 Compose 的 `0.0.0.0:9307:9307` 按要求保持原样。我认为问题在于它把无内置认证的服务发布到宿主机全部网卡，超出仅本机使用的监听边界。若目标是本机服务配合反向代理，建议最终改为 `127.0.0.1:9307:9307`。容器内 Uvicorn 的 `0.0.0.0` 用于 Docker 端口转发，与宿主机发布地址是两个层面。
