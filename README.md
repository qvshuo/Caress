# Caress

Emby 的 JAV 元数据刮削与封面裁剪工具。

## 部署

```sh
git clone https://github.com/qvshuo/Caress.git
cd Caress
cp docker-compose.example.yml docker-compose.yml
# 将 Compose 中的 /path/to/caress-data 改为宿主机数据目录
docker compose up -d --build
```

数据目录由容器用户 UID 1000 写入，容器内路径为 `/data`。无需挂载媒体目录。

## 配置

打开 `http://服务器地址:9307`，在设置中填写 Emby 地址与 API Key，点击连接并选择媒体库。按需配置来源优先级和翻译服务。

在 Emby 中关闭目标库的在线元数据提供者；本地 NFO、扫描及手动刷新仍可能更新元数据。

## 功能

从 Emby 读取作品，按文件名识别番号，依次查询 JAV 来源，完成翻译、演员匹配及自动封面裁剪后直接写回 Emby。支持批量补齐缺少的元数据、确认后替换受管元数据，以及交互式 2:3 封面裁剪。补齐模式保护已有有效内容，历史成功后也可补回缺失字段或封面；元数据和通用图片管理在 Emby 完成。
