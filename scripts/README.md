# 演员表维护

`update_actor_aliases.py` 用于抓取 JavDB 演员列表，离线生成 `caress/data/actor_aliases.json`，由开发者手动运行。

## 抓取

安装 uv 后，在仓库根目录运行。Python 3.15 和项目依赖由 uv 管理，环境、缓存及原始快照均放在仓库外。

```sh
uv run --no-project --python 3.15 --with-requirements requirements.txt scripts/update_actor_aliases.py fetch \
  --snapshots /path/outside/repository/actors-2026-10-31
```

每轮更新使用新目录，中断后用原目录续抓，已校验的快照会跳过。不要混用不同批次或语言的快照；损坏文件可移走后重抓。

脚本复用 `caress/settings.py` 的 `ApiSettings`，以 `en-US` 请求 `/api/v1/actors`，仅抓取 `type=0`（有码）和 `type=1`（无码）各 204 页，每页最多 50 条。请求间隔 10 秒，正常约需 68 分钟；连接错误、429 和 5xx 最多尝试 5 次，并遵守 `Retry-After`。

## 构建与更新

全部快照齐全后，先在仓库外生成候选文件，检查输出计数、SHA-256 和内容：

```sh
uv run --no-project --python 3.15 --with-requirements requirements.txt scripts/update_actor_aliases.py build \
  --snapshots /path/outside/repository/actors-2026-10-31 \
  --output /path/outside/repository/actor_aliases.candidate.json
```

确认后，省略 `--output` 更新仓库内的演员表，再检查 Git 差异：

```sh
uv run --no-project --python 3.15 --with-requirements requirements.txt scripts/update_actor_aliases.py build \
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

`scripts/update_catalog_rules.py` 提供同样的 `fetch` / `build` 流程，复用 `ApiSettings`。原始目录、作品分类证据和采集边界均保存在仓库外：

```sh
uv run --no-project --python 3.15 --with-requirements requirements.txt scripts/update_catalog_rules.py fetch \
  --snapshots /path/outside/repository/catalog-2026-11-04
uv run --no-project --python 3.15 --with-requirements requirements.txt scripts/update_catalog_rules.py build \
  --snapshots /path/outside/repository/catalog-2026-11-04 \
  --output /path/outside/repository/catalog_rules.candidate.json
```

- 目录来自 `/api/v1/series/letters`，每页 50 条，空页或重复页形成明确边界；这不保证覆盖来源未公开或新出现的全部前缀。
- **目录记录的 `type` 不是可靠的有码／无码依据**，不能直接写入规则。脚本对每个新增前缀取得可匹配的作品，再校验 `/api/v4/movies/{id}` 的番号、ID 和 `movie.type`；保留实际出现的完整数字前缀。前缀分类依据样本作品，不能保证同前缀的全部历史作品类型一致。
- 已核验的基础番号族保留在脚本 `SEEDS` 中。没有可验证作品的前缀保留未分类；分类冲突阻止替换规则，不静默选择一方。
- 默认请求间隔 5 秒，`--interval` 可调整为任意有限非负秒数；重试遵守 `Retry-After`，已有快照可断点续抓。每轮更新使用新目录，不混合不同轮次的快照。
- `build` 完全离线，校验目录页、边界和作品分类证据，按前缀排序生成 JSON。对同一批快照构建两次，确认 SHA-256 一致后，省略 `--output` 更新 `caress/data/catalog_rules.json`。

运行时优先查本地规则；未命中时才采用来源提供的分类。有码、无码、FC2 和其他作品均展示分类，缺少依据时显示未分类。其他与未分类使用设置中的“其他”路由；维护脚本不会作为服务任务自动运行，也不会修改已有 NFO。
