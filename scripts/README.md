# 本地数据维护

`update_javdb_data.py actors` 用于抓取 JavDB 演员列表，从零离线生成 `caress/data/actor_aliases.json`，由开发者手动运行。

## 抓取

安装 uv 后，在仓库根目录运行。Python 3.15 和项目依赖由 uv 管理，环境、缓存及原始快照均放在仓库外。

```sh
uv run --no-project --python 3.15 --with-requirements requirements.txt scripts/update_javdb_data.py actors fetch \
  --snapshots /path/outside/repository/actors-2026-10-31
```

每轮更新使用新目录，中断后用原目录续抓，已校验的快照会跳过。不要混用不同批次或语言的快照；损坏文件可移走后重抓。

脚本复用 `caress/settings.py` 的 `ApiSettings`，以 `en-US` 请求 `/api/v1/actors`，仅抓取 `type=0`（有码）和 `type=1`（无码）各 204 页，每页最多 50 条。请求间隔 10 秒，正常约需 68 分钟；连接错误、429 和 5xx 最多尝试 5 次，并遵守 `Retry-After`。

## 构建与更新

全部快照齐全后，先在仓库外生成候选文件，检查输出计数、SHA-256 和内容：

```sh
uv run --no-project --python 3.15 --with-requirements requirements.txt scripts/update_javdb_data.py actors build \
  --snapshots /path/outside/repository/actors-2026-10-31 \
  --output /path/outside/repository/actor_aliases.candidate.json
```

确认后，省略 `--output` 更新仓库内的演员表，再检查 Git 差异：

```sh
uv run --no-project --python 3.15 --with-requirements requirements.txt scripts/update_javdb_data.py actors build \
  --snapshots /path/outside/repository/actors-2026-10-31
git diff --stat -- caress/data/actor_aliases.json
```

构建规则：

- 按 ID 合并全部页面，第 200–204 页补尾部；重复 ID 采用后页记录，跨类型 ID 冲突则停止。
- 仅保留 `type`、日文主名 `name`、可选中文名 `name_zht` 和别名 `other_name`；丢弃空主名及无效类型，省略空可选字段。
- 别名按中英文逗号拆分，去空串、自名，并按 NFKC、空白归一化及大小写折叠后的结果去重、排序。
- 按 `(type, ID)` 排序，每人一行；相同快照生成相同字节。校验成功后原子替换，失败保留原文件。

保留快照和构建摘要，便于复现。发布前检查演员映射、头像匹配及翻译姓名保护。新表仅影响后续处理，不会重写现有 NFO；补齐缺失仍保留已有演员字段。

## 番号规则

`update_javdb_data.py catalog` 从 `/api/v1/series/letters` 抓取前缀目录，每页 50 条，遇到空页或重复页停止。仅按条目的 `letter` 和 `type` 从零生成 `caress/data/catalog_rules.json`，不读取旧规则、不补充种子、不查询作品详情。

```sh
uv run --no-project --python 3.15 --with-requirements requirements.txt scripts/update_javdb_data.py catalog fetch \
  --snapshots /path/outside/repository/catalog-2026-11-04
uv run --no-project --python 3.15 --with-requirements requirements.txt scripts/update_javdb_data.py catalog build \
  --snapshots /path/outside/repository/catalog-2026-11-04 \
  --output /path/outside/repository/catalog_rules.candidate.json
```

`type` 映射：`0` 有码、`1` 无码、`3` FC2、`2` 和 `4` 其他。前缀保留 API 的完整内容，只去除首尾空白并转大写；同前缀分类冲突则停止生成。目录未提供的前缀不会沿用旧规则。

默认请求间隔 5 秒，`--interval` 接受有限非负秒数。已有页面支持断点续抓，每轮更新使用新的仓库外目录。来源空页或重复页是采集边界，不保证覆盖全部前缀。

同一个脚本共用请求签名、连接、重试和限流处理，两个构建命令均完全离线，不读取已有输出。相同快照构建两次应字节一致；确认后省略 `--output` 更新仓库文件。运行时分类和演员主名仅依赖本地 JSON，未匹配的番号保留未分类，不用来源详情补判，也不自动改写已有 NFO。
