# 工程契约

Caress 只处理 Emby 无法提供的 JAV 数据获取、翻译、演员匹配和封面加工。Emby 拥有媒体、全部作品元数据、图片及手动修改。前端只保留作品与设置。

## 核心流程

Emby 所选媒体库分页读取 → 按视频/STRM 文件名识别番号 → 查询成功记录 → 依优先级准确命中一个来源 → 翻译与演员匹配 → 临时下载和 YuNet 裁剪 → 按每个 Item ID 分别写入 Emby → 回读验证 → 保存处理结果。

同一批次的同番号复用一次刮削及翻译。普通刮削跳过成功记录；完整流程失败后可重试；强制重刮及重试已有部分写入需要明确确认。手动裁剪独立于批次与成功记录。

## 职责

| 模块 | 责任 |
| --- | --- |
| `emby.py` | 服务器身份、分页、Item/人物/图片读取、受管字段更新和回读验证 |
| `library.py` | 实时只读投影、路径与关联验证、图片 tag URL，不缓存媒体库 |
| `identifiers.py` | 文件名主干、四类后缀、规范番号，保留现有媒体扩展名 |
| `catalog.py` / `actors.py` | 四类路由与保守的演员 ID/主名/别名规则 |
| `scraper.py` / `sources/` | 来源顺序查询、命中停止、身份校验和适配器 |
| `http.py` | Scrapling/FlareSolverr、连接复用、来源独占、主机限速与冷却 |
| `processing.py` | 每番号一次加工、临时图片、演员头像，只在刮削批次触发翻译验证 |
| `translation.py` | 标题/简介串行翻译、演员名和边界空白保护、失败回退 |
| `images.py` / `portraits.py` | 图片验证、YuNet、无重采样 2:3 PNG、临时 gfriends 头像 |
| `runtime.py` / `tasks.py` | SQLite 事务、一个四并发批次、Item 互斥、取消和重启恢复 |
| `service.py` / `api.py` | 生命周期、最新详情、有限期裁剪会话、HTTP 边界和秘密脱敏 |
| `import_settings.py` | 一次性备份及原子导入，不构成长期开关或兼容层 |
| Web `pages/` / `components/` / `lib/` | 作品与设置、共享控件和懒加载裁剪器、类型与传输 |

## 身份与安全写入

使用服务器 `System/Info.Id` 和 Item ID 定位。处理记录额外核对创建时间和番号，避免错误继承重建项目的记录。STRM 的 `Path` 用于识别；HTTP 播放地址不作为媒体文件路径。详情补读 `PartCount`、`AdditionalParts` 的完整 Item 和各 `MediaSources.ItemId`，执行前展开并去重。无法确定路径、创建时间或唯一番号时明确不支持。

每次写入先读完整 DTO，只覆盖来源非空的 Name、OriginalTitle、Overview、PremiereDate/ProductionYear、Studios、演员/导演、GenreItems、TagItems 和 caress Provider ID。保留其他 Provider ID、人物类型、现有标签、系统/用户字段以及 `LockData`/`LockedFields`。来源没有的数据不清空。标签合并保留，避免在没有归属证据时删除用户数据。

图片验证格式、尺寸及内容；上传 base64 与真实 MIME，再回读原始字节并比较像素。Primary 可替换；Backdrop 内容去重后追加，绝不依赖猜测的旧索引删除。人物只有可靠演员映射、明确 Emby Person ID 且无现有头像时才自动获取，同批次去重，失败仅警告。

Emby 没有跨 HTTP 请求的原子事务。失败可已部分写入，状态必须如实反映，重试重新执行并依内容去重。Caress 不宣称回滚外部服务器。

## 最小持久化

| 表 | 内容 |
| --- | --- |
| `options` | 设置，密钥仅服务端保存 |
| `rules` | 番号分类与演员匹配所需规则 |
| `scrapes` | 服务器/Item、身份摘要、番号、成功时间、最近结果/原因与成功来源 |
| `batch` | 唯一当前/最近批次的参数、计数所需 Item 状态及失败原因 |
| `cooldowns` | 来源与请求主机的必要冷却 |

没有作品元数据、媒体路径清单、来源候选、图片归属账本、编辑保护、翻译指纹或历史任务事件。成功记录不会因 Emby 手动编辑而失效；强制处理失败仍保留上一次成功时间。中断时记录失败或取消，已确认成功不丢失；缺席 Item 不自动删除历史记录。

图片在系统临时目录处理，每个番号关联全部完成后清理，批次结束兜底清理。裁剪会话至多四个、十分钟过期，关闭/保存清理，最终海报只在 Emby。原图优先直接读取Emby 横向背景，无可用背景时访问来源；来源也可显式重取，初始居中选框不加载 YuNet；从不使用 Primary 假装恢复原图。

## HTTP 接口

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 容器健康与版本 |
| `GET/PUT /api/settings` | 脱敏设置和保存；仅保留 Emby、翻译、来源路由/地址和 FlareSolverr，空缺密钥保留；source_defaults 只读提供内置来源地址 |
| `POST /api/settings/test/{emby,translation}` | 独立连接/翻译测试，不读取媒体库，不回传密钥 |
| `POST /api/settings/libraries` | 按当前表单连接读取媒体库，与连接测试分开 |
| `GET /api/works?q=&status=&offset=&limit=` | 从 Emby 分页批读，再搜索/筛选并分页响应；附带轻量导航顺序 |
| `GET /api/works/{id}` | 定向验证所选库归属，读取最新完整只读详情、版本与分集 |
| `GET/POST /api/batch` | 当前批次；普通、强制或完整重试 |
| `POST /api/batch/cancel` | 取消并等待清理 |
| `GET /api/images/{id}/{Primary,Backdrop}/{index}?tag=` | 固定 Emby 目标的限量图片代理，图片 tag 失效缓存 |
| `POST /api/works/{id}/crop/source` | 创建临时原图会话，可指定整个作品来源 |
| `POST /api/works/{id}/crop/{preview,apply}` | 2:3 预览或独立上传 Primary |
| `GET /api/works/{id}/crop/{token}/image` | 读取会话原图 |
| `DELETE /api/works/{id}/crop/{token}` | 关闭并清理会话 |

没有扫描、目录浏览、元数据保存、字段来源混搭、演员管理或历史任务接口。没有任意 URL 代理、任意本地路径读取或额外账户系统。

## 验证依据

以真实 Emby 4.10.2.0 的 Swagger 和写入回读为主要证据，结合官方 [Item Information](https://dev.emby.media/doc/restapi/Item-Information.html)、[Items](https://dev.emby.media/reference/RestAPI/ItemsService/getItems.html) 与 [Metadata Manager](https://www.emby.media/support/articles/Metadata-manager.html)。关闭在线提供者不等于禁止本地 NFO、扫描或手动刷新；配置和验收必须明确此边界。

## 前端交互

只保留表格；作品与设置等宽。批量模式独立选择，执行按钮固定为“开始”。刷新统一使用图标与“刷新”；详情顶部为“← 上一个作品 / 返回 / 下一个作品 →”，沿用当前搜索及筛选顺序，返回保留页码。状态用胶囊呈现，不展示内部 Item ID 或成功时间。头像有固定占位，封面与剧照使用 Base UI 对话框浏览，支持方向键。裁剪以选框、实时预览和操作按钮引导，只保留必要错误。

密钥以纯展示掩码表示已有配置，掩码不进入保存请求；来源地址显示实际配置或内置值。没有新增长期前端状态或媒体缓存。

## 最终打磨约束

Service 持有 Emby 连接池，首次使用验证服务器；列表、详情、图片、裁剪和批次共享租用。配置变更立即切换新请求，旧连接在活动租用结束后关闭；服务关闭先取消批次再关闭客户端。不跟随重定向，不把密钥放进 URL。

批次只读取一次所选库列表作为展开范围，逐项去重；越界或无法确认的关联项单独跳过。慢速刮削完成后，再以 ParentId/Ids 定向复核归属及完整 Item 身份，才进入写入。AdditionalParts 返回关联列表，不在客户端重复补读详情。

前后导航由已有列表响应提供 id/label/position 顺序，仅保存在当前页面会话；详情刷新始终读取 Emby，不为导航重新构建整库投影。直接打开详情且没有列表上下文时，不猜测相邻项。

成功历史与最近结果分别沿用 succeeded_at/status；派生“重刮失败”，取消独立显示。写入边界在当前批次及 running 记录原因中保留，取消/重启据此区分尚未写入与可能部分写入，无新增数据库表或字段。

背景去重仍以像素内容为准；Emby 图片 tag 不被当作跨图片内容摘要。来源摘要按本批次加工结果复用，已有背景逐张处理、同字节复用解码摘要，以条带 RGBA 摘要降低内存峰值。头像在锁内处理，确认成功或无需处理后才标记完成，单人物每批最多两次尝试，下载错误不缓存为完成。

裁剪原图经过资源验证；无 EXIF 变换时保留 JPEG/PNG/WebP 原字节，需要纠正方向时才转 PNG。YuNet 在缩小图像后建立数组，最终裁剪仍为无重采样 2:3 PNG，仅写 Primary。
