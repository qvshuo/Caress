# Caress

Emby 的 JAV 元数据刮削与封面裁剪工具。

- **作品识别**：从 Emby 读取作品，根据媒体文件名识别番号。
- **元数据刮削**：按来源优先级获取作品信息，支持翻译和演员匹配。
- **智能封面**：自动裁剪封面，并获取背景图和演员头像。
- **图片管理**：在 Emby 中管理图片，或交互式裁剪封面并保存至 Emby。

## 部署

```sh
git clone https://github.com/qvshuo/Caress.git
cd Caress
docker compose up -d --build
```

## 配置

打开 `http://服务器地址:9307`，填写 Emby 地址与 API Key，连接并选择需要刮削的媒体库。按需配置 AI 翻译服务和来源优先级。

在 Emby 中关闭目标库的在线元数据提供者；本地 NFO、扫描及手动刷新仍可能更新元数据。
