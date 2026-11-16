# 静态前缀建表

前缀分类只使用两个来源：JavDB 公开有码分类页，以及 AVSOX 公开影片列表和搜索记录。运行时只加载 `caress/data/catalog_rules.json`，不在线更新。

有码 714 条，无码 72 条。FC2、纯数字番号和 HEYZO 四位编号由代码识别，不重复入表。完整前缀精确匹配，数字命名空间和复合前缀保持完整。

名单不再继承旧表的无证据条目，也不使用 Amane、JavBoss 或 App API 的分类判断。此前用其他项目提出的搜索关键词所取得的 AVSOX 实际作品记录仍是 AVSOX 证据；关键词本身不构成入表依据。

`DSAM`、`HAME`、`HHF` 在两站记录中存在冲突，因此从两类白名单排除，运行时为 UNKNOWN。`EMP` 原先仅与旧表分类冲突，两站记录中只有 AVSOX 无码证据，现在归为无码。

[catalog_samples.json](../tests/fixtures/catalog_samples.json) 保存每个完整前缀、每种观测分类的一条作品证据，包括冲突双方。它可以独立重建当前名单，不能删掉冲突证据后再建表。网站分类和单部作品不能保证覆盖该前缀所有作品；未收录或冲突保持 UNKNOWN。此前的样本内统计不作为新名单的全站覆盖率或准确率。

## 手动采集与建表

在仓库根目录，使用现有 Python 依赖运行；输出和临时数据放仓库外：

```sh
export PYTHONPATH=.
uv run --no-project --python 3.15 --with-requirements requirements-dev.txt python scripts/catalog.py collect javdb --pages 1 2 10 50 100 --output /tmp/javdb-catalog.json
uv run --no-project --python 3.15 --with-requirements requirements-dev.txt python scripts/catalog.py collect avsox --pages 1 2 10 50 100 --output /tmp/avsox-catalog.json
uv run --no-project --python 3.15 --with-requirements requirements-dev.txt python scripts/catalog.py build tests/fixtures/catalog_samples.json /tmp/javdb-catalog.json /tmp/avsox-catalog.json --rules /tmp/catalog_rules.json --samples /tmp/catalog_samples.json
```

页码由操作者显式指定；示例不是全站采集范围。JavDB 使用 `/censored?vft=0&page=N` 并检查选中的有码分类和作品区域；AVSOX 使用其公开前端的 `/javu/data/api/getMovies` 列表接口。页间间隔两秒，无自动重试。遇到重定向、认证、HTTP 错误或无法识别的结构即失败，不写出本次采集结果，也不绕过限制。

建表只接受两站对应类别的记录，规范化番号、保留每个前缀两侧证据，再排除所有冲突。重新采集时将已有样本一并输入，避免丢失已知冲突。先检查临时输出及冲突报告，再手动替换正式 JSON 和样本，执行 Ruff 和 pytest 后提交。没有定时任务、GitHub Actions 采集或运行时自动更新。

仅重建现有名单：

```sh
PYTHONPATH=. uv run --no-project --python 3.15 --with-requirements requirements-dev.txt python scripts/catalog.py build tests/fixtures/catalog_samples.json --rules /tmp/catalog_rules.json --samples /tmp/catalog_samples.json
```

来源网页或公开接口可能变化，重新采集前需检查实际访问条件。采集结果只影响来源路由，不参与 DMM CID 或其他来源的作品身份判断。
