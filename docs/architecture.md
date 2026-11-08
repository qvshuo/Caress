# 架构契约

## 审计与收敛方向

现有适配器、文件名识别、翻译分段、YuNet 裁剪和 REST 传输继续保留。
确认需要修复的边界：来源和最终 Metadata 混用、TaskQueue 编排作品处理、API 中的完整保存事务、嵌套事务提前发布缓存、图片保护重复表达、配置 idle 回调、Emby 计划和写入耦合。

## 领域与所有权

| 概念 | 数据 | 权威维护者 |
| --- | --- | --- |
| Work | 标准番号、分类、当前输出 | RuntimeStore；扫描发现身份 |
| Media | 完整路径、原文件名、字幕/破解/分集 | Scanner → RuntimeStore.media |
| SourceMetadata | 来源字段和图片候选 | 来源适配器 → variants |
| WorkMetadata | Emby 可写字段，不含图片候选 | WorkProcessor / WorkOperations → RuntimeStore |
| Selection / Provenance | 人工字段保护、来源、固定原文、处理指纹 | 作品操作 → selections |
| Artwork | 每个文件角色的选择、保护、处理输入和状态 | artwork.py → selections.artwork |
| Task | 操作参数、历史进度和执行结果 | TaskQueue → tasks / task_items |
| EmbyAssociation | 每个媒体的关联和同步结果 | EmbySync → media.sync |

数据库是唯一持久化事实来源。任务和扫描内存投影必须可重建，不得在外层事务提交之前发布。任务历史不是作品健康状态；Emby 失败不改变刮削成功结果。扫描不覆盖执行期间产生的新输出。

## 处理阶段

| 阶段 | 输入 → 输出 | 副作用及恢复 |
| --- | --- | --- |
| Resolve | Work → 分类、路由 | 仅成功的精确分类写 rules |
| Fetch | 路由 → SourceResult[] | 候选写 variants，失败与未找到分开 |
| Select | 排序候选、人工选择 → 单一来源 | 不自动拼接来源 |
| Transform | 固定原文 → 最终字段及溯源 | 翻译串行；失败保留有效旧值 |
| Persist | 最终字段、溯源 → SQLite | 同一事务提交 |
| Artwork | 每角色输入 → 验证过的文件、状态 | 文件回滚包围数据库提交；保存输入后可独立重试 |
| Sync | 当前输出 → Emby | Resolve → 纯数据 Plan → Apply → Verify |

fill_missing 更新自动字段并保留人工保护和显式空值；refresh 重置保护并替换受管输出。remap_actors 离线使用保存的身份；update_portraits 不执行翻译和作品刮削。

## 生命周期

配置保存在 options，批次使用稳定快照。新配置只在批次边界激活，旧客户端关闭后重建。资源随服务或批次退出关闭。服务写锁和作品锁保留，不增加调度框架。

## 前端契约

components/ui 是唯一 Headless UI 边界。实际迁移范围为 Dialog、Select、Checkbox、Button；Badge 使用语义化 span。保留 CVA 样式和现有页面布局。采用 Base UI 的 render、Backdrop / Popup、Positioner / List、checked / indeterminate 和 data 属性，不保留 Radix 适配层。

前端只拥有未提交草稿和交互状态；轮询更新服务端投影，不覆盖草稿。不存在的 Menu、Popover、Tooltip、Radio、Tabs、Accordion、ScrollArea 不为本次迁移新增。

## 验证边界

自动测试使用临时 SQLite、临时图片及 HTTP mock；不连接真实刮削站点。测试源码纳入 Git，环境、缓存、产物在仓库外，生产镜像排除测试。真实 9307 不参与部署；9308 使用独立数据库。真实 Emby 的外部写入必须可回读、可恢复，测试不能把现有媒体当作可丢弃数据。
