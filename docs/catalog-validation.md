# 静态前缀核验

2026-11-16，基于 Caress 2.1.1 的 `f114ad88` 前缀表扩充。运行时只使用 `caress/data/catalog_rules.json`；本记录和测试样本不参与运行时分类。

## 数据与匹配

- 有码前缀 **747** 条，无码前缀 **72** 条，均为完整前缀，不使用包含关系或逐级缩短匹配。
- FC2、纯数字番号和 HEYZO 四位编号继续由代码判断，不重复保存为前缀条目。
- 有码示例：`SSIS`、`ABP`、`MIDA`、`DLDSS`、`3DSVR`、`T38`。
- 无码示例：`CWP`、`CWPBD`、`MKBD-S`、`S2M`、`SKYHD`、`NYOSHIN-N`、`4198-R`。
- 数字前缀和复合前缀保持完整；`3DSVR` 不匹配 `13DSVR`，`MKBD-S` 不退回 `MKBD`。前缀分类不更改番号，不参与 DMM CID 或其他来源的身份核对。

## 核验依据与限制

参考 [JavBoss 的 JavDB App API](https://github.com/Solr159/JavBoss/blob/5046258ad399ce4e2900195456950020e57f8b6c/internal/jav/scrape/javdbapi/api.go)：其作品类型使用 `0`/`1` 区分有码和无码，客户端需要 `jdsignature`。本次未签名的 `/api/v2/search?type=code&q=SSIS` 返回 `ParameterInvalid`，要求 `jdsignature`；未复制签名实现，未取得搜索前缀或 `/api/v4/movies/{id}` 的实测分类数据。

[Amane 的规则](https://github.com/sqzw-x/amane/blob/391af4fdc9fec4c7eb279ce8de53fbf693f09d5a/src/amane/parsing/file_info.py) 用于提出候选和交叉检查。没有采用其普通番号默认有码、字符串开头匹配或数字命名空间补写逻辑。搜索命中其他编号不证明候选前缀有效，例如 `XXX-AV` 搜索只返回 `AV9898-363`，未据此加入 `XXX-AV`。`YKB` 没有核验记录，也未加入。

新增前缀依据以下实际公开记录：

- [JavDB 有码分类页](https://javdb.com/censored?vft=0&page=1)：按固定页码覆盖近期和较早记录，检查实际选中的有码标签及作品区域。JavDB 无码入口重定向登录，未继续该路径。
- [AVSOX](https://avsox.click/cn)：正常浏览器加载其公开页面，确认无需认证的影片列表与搜索请求，再离线读取相同接口。核验了 Amane 的候选，覆盖近期及较早记录。该站同时收录 FC2，FC2 标准番号按独立家族统计。

仅接受现有番号规范化能够处理的记录，保留复合和数字前缀。公开记录去重后共 **6,349 条（番号、来源分类）记录**。每个有新核验记录的入表前缀保留一个实际番号及作品 URL，见 [catalog_samples.json](../tests/fixtures/catalog_samples.json)。另有原表的 33 个有码前缀及 `LEGSJAPAN` 沿用 2.1.1，本次没有取得新的对应样本；它们不是新增前缀。

## 分类冲突

以下前缀不进入任何白名单，保持 UNKNOWN，不在运行时增加冲突状态或在线判断：

| 前缀 | 依据一 | 依据二 |
| --- | --- | --- |
| DSAM | [DSAM-020，有码分类页记录](https://javdb.com/v/kKawdm) | [AVSOX 无码记录 DSAM-157](https://avsox.click/cn/movies/kxzzrxo) |
| EMP | 2.1.1 标为有码 | [AVSOX 无码记录 EMP-007](https://avsox.click/cn/movies/namldrn) 等 EMP-001 至 EMP-009，Amane 也列为无码候选 |
| HAME | [JavDB 有码记录 HAME-091](https://javdb.com/v/ZN9b0X) | [AVSOX 无码记录 HAME-028](https://avsox.click/cn/movies/klqqmgb) |
| HHF | [JavDB 有码记录 HHF-121](https://javdb.com/v/r3VO7R) | [AVSOX 无码记录 HHF-090526](https://avsox.click/cn/movies/keglagz) |

来源有分歧时不按某一来源或某条旧规则强行定类。

## 同一核验集比较

覆盖率为已分类记录数 / 全部记录数；准确率为已分类结果与核验来源分类一致的比例，UNKNOWN 不计为正确结果。

| 规则 | 已分类 | UNKNOWN | 覆盖率 | 已分类一致率 |
| --- | ---: | ---: | ---: | ---: |
| 2.1.1 前缀表及当时家族规则 | 2,750 | 3,599 | 43.31% | 99.67% |
| 2.2.0 仅明确家族 | 1,871 | 4,478 | 29.47% | 100% |
| 本次静态白名单 | 6,257 | 92 | 98.55% | 100% |

原表的 9 条不一致记录均来自 EMP。新增白名单排除了已发现的冲突，没有强行消除 UNKNOWN。

这是**建表与交叉核验样本内的结果**，不是全站准确率估计。样本包含本次建表依据，核验阶段确认的新前缀也已入表；不能将其描述为未参与建表的独立测试集。两站的分类也可能存在遗漏或错误，不保证覆盖所有番号。未收录前缀继续使用 UNKNOWN 来源顺序。

## 回归边界

测试验证四类自定义来源顺序、文件属性与发行分类分离、前缀精确边界、表内无冲突及实际作品样本。DMM 原有严格 CID 匹配独立保留，`MIUM-123` 与 `300MIUM-123` 不会因分类表而被视作同一作品。离线采集脚本不纳入项目，不增加动态更新、数据库或在线 App API 请求。
