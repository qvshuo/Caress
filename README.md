# Caress

Emby 的 JAV 元数据刮削与封面裁剪工具。

- **作品识别**：从 Emby 读取作品，根据媒体文件名识别番号。
- **元数据刮削**：按来源优先级获取作品信息，支持翻译和演员匹配。
- **智能封面**：自动裁剪封面，并获取背景图和演员头像。
- **灵活更新**：支持补齐缺失的元数据，或替换已有的受管元数据。
- **手动裁剪**：支持交互式 2:3 封面裁剪，直接保存至 Emby。

## 部署

```sh
git clone https://github.com/qvshuo/Caress.git
cd Caress
cp docker-compose.example.yml docker-compose.yml
# 将 Compose 中的 /path/to/caress-data 改为宿主机数据目录
docker compose up -d --build
```

数据目录须允许 UID 1000 写入；无需挂载媒体目录。

## 配置

打开 `http://服务器地址:9307`，在设置中填写 Emby 地址与 API Key，点击连接并选择媒体库。按需配置来源优先级和翻译服务。

在 Emby 中关闭目标库的在线元数据提供者；本地 NFO、扫描及手动刷新仍可能更新元数据。
