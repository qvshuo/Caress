# Caress

适用于 Emby 的 JAV 元数据刮削与封面处理工具。

## 功能

- **作品识别**：自动识别 Emby 媒体库中的 JAV 作品；
- **元数据刮削**：自动获取作品信息，支持中文翻译和演员名称匹配 ；
- **封面处理**：自动获取并裁剪封面，支持手动调整，同时补充背景图和演员头像。 

## 部署

```sh
git clone https://github.com/qvshuo/Caress.git
cd Caress
docker compose up -d --build
```

## 配置

打开 `http://服务器地址:8098`，填写 Emby 地址与 API Key，连接并选择需要刮削的媒体库。按需配置 AI 翻译服务和来源优先级。
