# Caress

Emby 的 JAV 刮削与封面裁剪工具。Emby 保存媒体、元数据和图片；Caress 负责番号识别、多来源刮削、AI 翻译、演员匹配及 YuNet 封面裁剪。

## 快速开始

需要 Docker 与 Docker Compose。

```sh
cp docker-compose.example.yml docker-compose.yml
# 将 /path/to/caress-data 改为宿主机实际数据目录
docker compose up -d --build
```

打开 `http://服务器地址:9307`，在设置中填写 Emby 地址、API Key，测试连接并选择媒体库。可配置来源顺序、FlareSolverr 及兼容 OpenAI 的翻译服务。连接测试与媒体库读取分别操作，来源优先级用英文逗号分隔。

宿主机数据目录映射到容器 `/data`，SQLite 保存设置、匹配规则和处理记录。Caress **无需挂载媒体目录**，不会读取或修改媒体文件；Emby 自行管理视频及 STRM。图片只临时下载，最终保存在 Emby。修改 Compose 挂载或端口后执行 `docker compose up -d --force-recreate`。

## 使用

- **作品**：从 Emby 读取列表与详情，支持表格、搜索、状态筛选、批量刮削、失败重试和取消。
- **普通刮削**跳过已有成功记录的 Item，保护后续在 Emby 中的手动修改。**强制重刮**需确认，只更新来源提供的受管字段和图片。
- **封面裁剪**优先直接读取 Emby 横向背景图并提示检查画面完整性；无可用背景时获取来源原图，也可手动重新获取。裁剪固定 2:3，仅替换 Primary，不改变刮削记录。
- 元数据、演员资料及通用图片管理在 Emby 完成，刷新 Caress 即可看到最新数据。Emby 离线时显示连接错误。

请自行关闭目标媒体库的在线元数据提供者。Caress 不改变 `LockData` 或字段锁定；**本地 NFO、自动扫描、手动刷新和重新入库仍可能影响元数据**。

## 旧配置

请先备份旧数据和 Compose，并为新版使用全新数据目录。将旧 JSON 配置或 SQLite 复制到新目录后执行：

```sh
docker compose run --rm --no-deps caress python -m caress.import_settings /data/old-settings.json /data/caress.sqlite3
```

导入工具另行备份原文件，迁入可验证的连接、来源、翻译及匹配规则。旧作品、图片和阶段记录不迁入；无法证明当前 Item 身份与完整写入的旧成功标记不继承。首次执行批次前核对选择，导入不会修改 Emby。

[工程契约](docs/architecture.md) · [重构交付与验收](docs/delivery.md)
