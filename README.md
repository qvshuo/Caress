<p align="center">
  <img src="erosia/web/assets/favicon.svg" alt="erosia" width="96" height="96" />
</p>

<h1 align="center">erosia <small>/ɪˈrəʊziə/</small></h1>

刮削 JAV 视频元数据。

- 多源刮削：支持 DMM、JavDB、JavBus 等来源；
- 配套插件：提供 Emby 插件直接读取本地 NFO、封面、剧照和演员头像；
- 演员头像：从 gfriends 获取头像，支持日文主名、中文名和别名匹配；
- 封面裁剪：检测人脸并自动裁剪适用于 Emby 的 2:3 封面；
- 自动翻译：配置 OpenAI 兼容 API 后，自动翻译作品标题与剧情；
- 标准格式：使用符合 Emby 规范的 NFO，图片独立保存。

## 部署

```sh
git clone --depth=1 https://github.com/qvshuo/erosia.git && cd erosia
mkdir -p data
cp docker-compose.example.yml docker-compose.yml

# 修改 docker-compose.yml 中的作品目录和数据目录后启动
docker compose up -d --build
```

打开 `http://服务器IP:9307`。

## 使用

1. 设置作品目录和元数据目录；
2. 点击 作品 - 扫描作品，视频或 STRM 文件的父文件夹名称须为完整番号，且不能重复；
3. 勾选作品，选择 补齐缺失 或 重新刮削，点击 刮削元数据。

### 插件

1. 下载 [erosia.dll](emby-plugin/erosia.dll?raw=true)，放入 Emby 配置目录下的 `plugins` 文件夹；Docker 部署时，对应 Emby 容器内的 `/config/plugins`；

2. 在 Emby 的 Compose 服务中挂载元数据目录，左侧填写 erosia 元数据目录的宿主机绝对路径：

   ```yaml
   volumes:
     - /path/to/data/metadata:/erosia-metadata:ro
   ```

3. 在 Emby 的 Compose 目录执行以下命令，应用挂载并加载插件；

   ```sh
   docker compose restart
   ```

4. 在媒体库的元数据读取器中启用 **erosia**，随后刷新媒体库。

插件直接读取本地文件，无需连接 erosia 服务。运行期间监听 `/erosia-metadata` 内的元数据变更并自动刷新；首次接入仍需刷新媒体库。
