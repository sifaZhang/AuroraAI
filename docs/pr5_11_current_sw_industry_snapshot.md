# PR5.11 申万当前行业树与股票归属持久化 V1

## 目标与范围

本版本将统一 Provider 返回的申万2021一级、二级、三级行业树和股票当前归属持久化到 SQLite，为后续板块雷达提供稳定主数据。

本版本只维护当前行业归属，不保存历史成员关系；只输出冲突日志和可选 JSON，不建立冲突数据库。它不修改板块评分、前端、首板回调策略或旧 `sector_memberships` 数据。

## 数据表

迁移 `023_current_sw_industry_snapshot.sql` 创建：

- `industry_nodes`：当前申万行业树，主键为分类、版本和行业代码，含层级、父代码、来源和更新时间。
- `industry_memberships_current`：每只股票至多一条当前完整三级归属，主键为分类、版本和股票代码。

行业节点具有父节点自外键；归属的一级、二级、三级代码均外键关联同版本行业节点。查询索引覆盖层级、父节点和三级行业代码。

## 当前归属与冲突

同步只处理 `is_current=True` 的成员。完全相同的重复行会去重并计入 `duplicate_count`。

同一股票存在两个或更多不同三级链路时视为冲突：

- 不按顺序、日期或名称相似度选择；
- 不写入该股票；
- 其他正常股票继续写入；
- 返回 `partial_success`；
- 将股票和全部候选链记录到结构化日志；
- 可通过 `--export-conflicts` 输出 JSON。

行业代码对应不同名称、层级或父节点属于行业树冲突，整次同步失败且不修改旧快照。

## 原子替换与幂等

Repository 在同一个 `BEGIN IMMEDIATE` 事务中：

1. 创建临时节点和归属表；
2. 批量写入并校验行数；
3. 检查所有归属代码在临时行业树中存在；
4. 删除正式表旧快照；
5. 批量复制新节点和归属；
6. 提交。

任何异常都会回滚，节点和归属不会出现一新一旧。正式替换前按稳定字段集合比较；完全一致时 `changed=False` 且不刷新 `updated_at`。`--force` 会强制替换。

## 数据源行为

默认链路为 Tushare → AKShare。Tushare成功时不调用AKShare。

PR5.11允许全量 Provider 返回冲突候选，冲突只能由同步服务分组、审计和跳过；单股 Provider 查询仍对多归属严格失败。

当前AKShare三级目录可用，但三级成分接口因上游17列变化为18列而 degraded。若备用源不能返回完整当前三级归属，同步失败，不会写入空归属。

## CLI

只读真实校验：

```powershell
python -m backend.data_sources.cli sync-industries --dry-run
```

正式同步和强制替换：

```powershell
python -m backend.data_sources.cli sync-industries
python -m backend.data_sources.cli sync-industries --force
```

指定来源和导出冲突：

```powershell
python -m backend.data_sources.cli sync-industries --provider tushare --dry-run --export-conflicts conflicts.json
```

数据库只读查询：

```powershell
python -m backend.data_sources.cli db-symbol-industry --symbol 600519.SH
python -m backend.data_sources.cli db-industry-constituents --industry-code 851251 --level 3 --limit 20
```

退出码：0为成功，1为存在已跳过冲突的部分成功，2为失败或参数错误。

dry-run真实调用Provider并完成标准化、行业树校验、重复/冲突检测和预计变更比较，但不迁移或写入正式数据库，也不更新时间戳。

## 测试与已知限制

离线测试覆盖迁移幂等、索引、Repository读写、三级查询、原子回滚、幂等、force、dry-run、冲突和CLI退出码。

已知限制：

- 无历史行业成员关系和历史快照；
- 无冲突数据库及人工覆盖；
- 冲突股票暂时跳过；
- AKShare三级成分仍为 degraded；
- Tushare首次全量分批约需152秒；
- 当前同步为前台CLI，不实现后台任务。

2026-08-01真实dry-run结果：Tushare无降级；行业树共497个节点（31个一级、130个二级、336个三级）；输入5924行；5808只股票可写；58只股票存在双current归属并被跳过；另排除 `T00018.SH` 非股票成员。dry-run未写入行业数据。

后续PR可增加历史快照、冲突复核、行业日行情及板块评分接入。
