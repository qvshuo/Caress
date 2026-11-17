# 静态前缀建表

前缀分类只使用两个来源：JavDB 公开有码分类页，以及 AVSOX 公开影片列表和搜索记录。运行时只加载 `caress/data/catalog_rules.json`，不在线更新。

有码 727 条，无码 74 条，`updated_at` 为 `2026-11-17`。FC2、纯数字番号由代码识别；HEYZO、HEYDOUGA 与其他前缀统一由数据表分类。完整前缀精确匹配，数字命名空间和复合前缀保持完整。

名单不再继承旧表的无证据条目，也不使用 Amane、JavBoss 或 App API 的分类判断。此前用其他项目提出的搜索关键词所取得的 AVSOX 实际作品记录仍是 AVSOX 证据；关键词本身不构成入表依据。

`DSAM`、`HAME`、`HHF` 在两站记录中存在冲突，因此从两类白名单排除，运行时为 UNKNOWN。`EMP` 原先仅与旧表分类冲突，两站记录中只有 AVSOX 无码证据，现在归为无码。

[catalog_samples.json](../tests/fixtures/catalog_samples.json) 保存每个完整前缀、每种观测分类的一条作品证据，包括冲突双方。它可以独立重建当前名单，不能删掉冲突证据后再建表。网站分类和单部作品不能保证覆盖该前缀所有作品；未收录或冲突保持 UNKNOWN。此前的样本内统计不作为新名单的全站覆盖率或准确率。

## 手动采集与建表

在仓库根目录，使用现有 Python 依赖运行；输出和临时数据放仓库外：

```sh
export PYTHONPATH=.
uv run --no-project --python 3.15 --with-requirements requirements-dev.txt python scripts/catalog.py collect javdb --pages 1 2 3 5 10 20 30 50 75 100 --output /tmp/javdb-catalog.json
uv run --no-project --python 3.15 --with-requirements requirements-dev.txt python scripts/catalog.py collect avsox --pages 1 2 3 5 10 20 50 100 200 500 --output /tmp/avsox-catalog.json
uv run --no-project --python 3.15 --with-requirements requirements-dev.txt python scripts/catalog.py build tests/fixtures/catalog_samples.json /tmp/javdb-catalog.json /tmp/avsox-catalog.json --rules /tmp/catalog_rules.json --samples /tmp/catalog_samples.json
```

页码由操作者显式指定；示例不是全站采集范围。JavDB 使用 `/censored?vft=0&page=N` 并检查选中的有码分类和作品区域；AVSOX 使用其公开前端的 `/javu/data/api/getMovies` 列表接口。页间间隔两秒，无自动重试。遇到重定向、认证、HTTP 错误或无法识别的结构即失败，不写出本次采集结果，也不绕过限制。

建表只接受两站对应类别的记录，规范化番号、保留每个前缀两侧证据，再排除所有冲突。重新采集时将已有样本一并输入，避免丢失已知冲突。先检查临时输出及冲突报告，核验名单与样本一致并执行检查。只有正式名单发生变化且验证通过，才手动将正式 JSON 的 `updated_at` 更新为当天日期（YYYY-MM-DD）；无变化、采集失败或仅生成临时文件均不推进日期。脚本对已有输出保留原日期，新临时输出不生成日期。再提交正式名单与样本。没有定时任务、GitHub Actions 采集或运行时自动更新。

仅重建现有名单：

```sh
PYTHONPATH=. uv run --no-project --python 3.15 --with-requirements requirements-dev.txt python scripts/catalog.py build tests/fixtures/catalog_samples.json --rules /tmp/catalog_rules.json --samples /tmp/catalog_samples.json
```

来源网页或公开接口可能变化，重新采集前需检查实际访问条件。采集结果只影响来源路由，不参与 DMM CID 或其他来源的作品身份判断。


## 2026-11-17 十页实测

每页统计中的新番号、新前缀指本次运行内首次出现，不表示全部为正式白名单的新条目。有效页为非空且结构正确的页面，包括重复页；无效格式的番号不计入唯一番号。

| 来源 | 页码 | 有效番号 | 新唯一番号 | 新前缀 | 整页重复 |
| --- | ---: | ---: | ---: | ---: | --- |
| javdb | 1 | 40 | 40 | 16 | 否 |
| javdb | 2 | 40 | 40 | 20 | 否 |
| javdb | 3 | 40 | 40 | 19 | 否 |
| javdb | 5 | 40 | 40 | 19 | 否 |
| javdb | 10 | 40 | 40 | 19 | 否 |
| javdb | 20 | 40 | 40 | 20 | 否 |
| javdb | 50 | 40 | 40 | 18 | 否 |
| javdb | 100 | 39 | 39 | 22 | 否 |
| javdb | 200 | 39 | 0 | 0 | 是 |
| javdb | 500 | 39 | 0 | 0 | 是 |
| avsox | 1 | 52 | 52 | 6 | 否 |
| avsox | 2 | 55 | 55 | 2 | 否 |
| avsox | 3 | 57 | 57 | 8 | 否 |
| avsox | 5 | 49 | 49 | 0 | 否 |
| avsox | 10 | 52 | 52 | 0 | 否 |
| avsox | 20 | 51 | 51 | 0 | 否 |
| avsox | 50 | 53 | 53 | 0 | 否 |
| avsox | 100 | 60 | 60 | 1 | 否 |
| avsox | 200 | 60 | 60 | 0 | 否 |
| avsox | 500 | 52 | 52 | 10 | 否 |

JavDB：10 个非空有效页，其中 2 个重复页，319 个唯一番号、153 个运行内新前缀。第 200、500 页没有增加内容，不能算作更深范围的独立覆盖；后续示例调整为 `1 2 3 5 10 20 30 50 75 100`，补充实测第 30、75 页各有 40 个有效番号，二者不重复；仍须逐次检查统计。

AVSOX：10 个非空有效页，0 空页、0 重复页，541 个唯一番号、27 个运行内新前缀。本次第 100、200、500 页均有有效内容。

单站各自无冲突；与现有样本合并后仍有 DSAM、HAME、HHF 三个冲突前缀，保持 UNKNOWN。最终名单 727 个有码、74 个无码。

HEYDOUGA 补充核验：AVSOX 作品 `4037-428`（https://avsox.click/cn/movies/kjavgjp）明确带有 `from=heydouga` 生产者标识；采集时将此格式表达为 `HEYDOUGA-4037-428`，用于前缀建表，不改变运行时严格番号相等规则。HEYZO 来自实际 AVSOX 作品番号。代表性样本保留两站证据和冲突双方。
