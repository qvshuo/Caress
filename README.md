<p align="center">
  <img src="erosia/web/assets/favicon.svg" alt="erosia" width="96" height="96" />
</p>

<h1 align="center">erosia <small>/ɪˈrəʊziə/</small></h1>

刮削 JAV 视频元数据。

- 多源刮削：支持 DMM、JavDB、JavBus 等来源；
- 配套插件：Emby 插件直接读取本地 NFO、封面、剧照和演员头像；
- 演员头像：从 gfriends 获取头像，支持日文主名、中文名和别名匹配；
- 封面裁剪：支持人脸检测、居中和手动调整，生成 2:3 封面；
- 自动翻译：标题按「番号 演员名 译后标题」组装，剧情自动翻译；
- 标准格式：使用符合 Emby 规范的 NFO，图片独立保存。

## 部署

```sh
git clone --depth=1 https://github.com/qvshuo/erosia.git erosia && cd erosia
mkdir -p /path/to/data # 替换为实际的数据目录
cp docker-compose.example.yml docker-compose.yml

# 修改 docker-compose.yml 中的作品目录和数据目录后启动
docker compose up -d --build
```

打开 `http://服务器IP:9307`。

## 使用

1. 设置作品目录和元数据目录，点击 **扫描作品**。视频或 STRM 文件的父文件夹名称须为完整番号，且不能重复；
2. 勾选作品，选择 **补齐缺失** 或 **重新刮削**，点击 **刮削元数据**；
3. 在作品详情中查看结果、编辑字段或调整封面，修改后点击保存。

**补齐缺失**按当前路由首个成功来源更新自动字段和图片，保留手动修改、手动指定来源和自定义空值；例如 DMM 后续成功时，可替换原先的 JavDB 自动内容。**重新刮削**同时替换手动内容，来源没有系列时清空旧系列。系列可直接编辑。**更新头像**重新获取已有演员头像，不改 NFO；获取失败保留旧图。

详情页在图片下方按 NFO 顺序展示字段，「译」「主名」标记处理结果，后方展示原文；每组来源同步切换。原始标题、剧情和演员名保存在运行库，手动编辑不丢原文。演员页先勾选演员，再执行 **重新映射** 或 **更新头像**；重新映射根据最新演员 JSON 更新所选演员，优先使用已保存的演员 ID，保留手动字段，不重新刮削或翻译。

配置翻译模型和 API Key 后，每批先验证服务；标题先移除演员 JSON 中的主名和别名，翻译剩余文字再组装。未配置时使用清理后的原标题；服务故障显示部分完成，可重试失败阶段。站点间隔支持非负小数秒，留空用默认值，0 表示无间隔。换代理后可在来源设置中解除冷却。

### 接入 Emby

1. 下载 [erosia.dll](emby-plugin/erosia.dll?raw=true)，放入 Emby 配置目录下的 `plugins` 文件夹；Docker 部署时，对应容器内的 `/config/plugins`；

2. 在 Emby 的 Compose 服务中挂载元数据目录，左侧填写 erosia 元数据目录的宿主机绝对路径：

   ```yaml
   volumes:
     - /path/to/data/metadata:/erosia-metadata:ro
   ```

3. 在 Emby 的 Compose 目录执行以下命令，应用挂载并加载插件；`emby` 替换为实际服务名：

   ```sh
   docker compose up -d --force-recreate emby
   ```

4. 在媒体库的元数据读取器中启用 **erosia**，随后刷新媒体库。

插件直接读取本地文件，无需连接 erosia 服务。运行期间监听 `/erosia-metadata` 内的 NFO、封面、剧照和头像变更，合并文件事件后自动刷新对应作品或演员；首次接入仍需刷新媒体库。挂载目录须支持文件变更通知；网络文件系统不支持时需手动刷新。
