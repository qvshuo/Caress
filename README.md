<p align="center"><img src="assets/logo.png" alt="Caress" width="96" height="96" /></p>
<h1 align="center">Caress <small>/kəˈres/</small></h1>

扫描视频与 STRM，多源刮削元数据、自动翻译、匹配演员头像和裁剪封面，通过 Emby REST API 同步。

## 部署

```sh
git clone --depth=1 https://github.com/qvshuo/Caress.git caress && cd caress
mkdir -p data
cp docker-compose.example.yml docker-compose.yml
# 修改媒体目录和数据目录的宿主机路径
docker compose up -d --build
```

宿主机数据目录须允许容器内 UID 1000 读写。

打开 `http://服务器IP:9307`。在设置中选择容器内的媒体目录，填写 Emby 地址和 API Key，并测试连接。
Caress 和 Emby 容器必须将同一媒体目录挂载到相同的容器内绝对路径。例如 Emby 中为 `/path/to/media`，Caress 也应挂载到 `/path/to/media`。通过完整路径精确关联作品。

## 使用

- 扫描依据文件名主干识别番号，父目录名称不参与识别。仅移除末尾的 `-C`、`-U`、`-UC` 和 `-cdN` 后缀，忽略大小写；同番号的版本和分集共享元数据。
- 补齐缺失更新自动字段及图片，保留手动编辑；刷新替换所选来源的全部字段及图片。字段和图片可分别选择来源。
- 配置翻译后自动翻译标题与剧情。头像更新只更新所选演员头像。
- 保存或刮削后自动同步 Emby。未入库的媒体保持待同步；失败可手动同步重试，路径关联可重新建立。
- Emby 的类型和标签分别同步；字幕、破解标识写入标签。剧照与原始背景图使用有序 Backdrop 图片，封面和演员头像使用 Primary。
- 时长由 Emby 探测媒体；电影的系列名称不能直接写入，因此不刮削或编辑这些字段。

所有结构化数据保存在 `/data/caress.sqlite3`，图片保存在 `/data/images`。媒体只读。
生产环境由 Python 提供 Web 静态文件。本版本使用全新数据库，不导入旧版数据。


修改 Compose 的挂载后，执行 `docker compose up -d --build --force-recreate` 重新创建容器。
当前数据库结构版本为 2；旧结构会明确报错，不自动迁移。升级前备份原数据并使用新的宿主机数据目录验证。

架构、组件迁移、测试及部署验收见 [交付说明](docs/delivery.md)。测试源码纳入 Git，生产镜像不包含测试；本地环境、缓存和构建产物应放在仓库外。
