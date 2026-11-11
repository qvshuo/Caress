# Caress

适用于 Emby 的 JAV 元数据刮削与封面裁剪工具。

## 功能

- **作品识别**：从 Emby 读取作品，根据媒体文件名识别番号。
- **元数据刮削**：从多个来源获取作品信息，支持标题、简介翻译及演员名称匹配。
- **封面处理**：自动获取图片并裁剪封面，支持手动调整裁剪区域，同时补充背景图和演员头像。

## 部署

```sh
git clone https://github.com/qvshuo/Caress.git
cd Caress
docker compose up -d --build
```

## 配置

打开 `http://服务器地址:9307`，填写 Emby 地址与 API Key，连接并选择需要刮削的媒体库。按需配置 AI 翻译服务和来源优先级。
