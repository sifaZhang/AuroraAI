# PR5.10 统一市场数据访问层与 Tushare 主源接入

## 目标与边界

本模块为业务层提供稳定的行业数据接口，隔离 Tushare、AKShare 等第三方 SDK、DataFrame、字段命名和异常类型。本阶段只提供基础设施、健康检查和只读 CLI，不写正式数据库，不修改板块评分、板块雷达页面或首板策略。

数据源职责：

- Tushare：结构化基础数据主源，当前用于申万 2021 行业目录和成员关系。
- AKShare：备用和特色数据补充；三级目录可用，三级成分字段变化时明确降级。
- GM：继续负责实时、分钟及终端相关数据，本 PR 不迁移其调用。

## Provider 契约

业务代码依赖 `IndustryDataProvider`，获得 `IndustryNode`、`IndustryMembership` 和带来源、时间、行数、降级标记的 `ProviderResult`。Provider 内部可以使用 pandas，任何公开方法都不得返回 DataFrame。

统一能力包括：

- 行业目录查询；
- 当前或指定日期的成员关系；
- 股票行业归属；
- 行业成分列表；
- Provider 健康状态。

默认由 `build_industry_provider()` 创建 `tushare -> akshare` 主备链。业务模块不得自行实例化具体 Provider。

## 标准化规则

- 分类固定为 `SW`，版本固定为 `2021`。
- 行业级别仅允许 1、2、3；二三级节点必须拥有有效父节点。
- 项目内部继续使用既有证券代码格式 `600519.SH`、`000001.SZ`、`430047.BJ`。
- `SHSE.600519` 等 GM 格式只在数据源边界转换，不改变现有数据库和策略口径。
- 日期在 Provider 边界转换为 `datetime.date`。
- 当前每只股票最多有一个三级行业；冲突不会被静默去重。

## Tushare 实现

`TushareClient` 延迟创建 SDK 连接，负责 Token 检查、节流、超时、有限重试、异常转换和敏感信息脱敏。行业成员主接口为 `index_member_all`。

若无参数调用达到 2000 行边界，客户端通过 `index_classify` 获取三级代码，并按 `l3_code` 分批调用 `index_member_all`，合并、去重后再执行完整性校验。字段始终按名称读取，不依赖列顺序。

Token 只从环境变量读取，不写入源码、数据库或审计日志。

## AKShare 实现

行业目录使用：

- `sw_index_first_info()`
- `sw_index_second_info()`
- `sw_index_third_info()`

三级成分使用 `sw_index_third_cons()`。当前上游网页由 17 列变化为 18 列时，AKShare 1.18.69 会抛出列长度错误；适配器将其转换为 `ProviderSchemaError`，健康状态标记为 `degraded`，不会返回空列表冒充成功。

AKShare 只提供当前快照时，不允许用于历史时点成员查询。

## 降级与错误分类

允许自动降级：网络不可达、认证或权限不足、超时、临时限频、字段契约变化、语义上不允许为空的数据为空。

不允许自动降级：非法参数、不支持的行业层级、标准化错误和数据冲突。这些问题可能改变业务含义，必须直接失败。

所有 Provider 均使用统一异常：`ProviderUnavailableError`、`ProviderAuthenticationError`、`ProviderPermissionError`、`ProviderRateLimitError`、`ProviderTimeoutError`、`ProviderSchemaError`、`ProviderValidationError`、`ProviderEmptyDataError`。全部来源失败时抛出不包含原始响应的聚合异常。

自动主备调用输出结构化审计字段：操作、Provider、是否降级、起止时间、耗时、行数、成功状态、错误类型和校验状态。审计禁止记录 Token、完整响应和请求对象。

## 环境变量

```text
TUSHARE_TOKEN=
TUSHARE_ENABLED=true
TUSHARE_PRIMARY=true
AKSHARE_FALLBACK_ENABLED=true
DATA_SOURCE_REQUEST_TIMEOUT_SECONDS=30
DATA_SOURCE_MAX_RETRIES=2
DATA_SOURCE_REQUESTS_PER_MINUTE=180
INDUSTRY_PRIMARY_PROVIDER=tushare
INDUSTRY_FALLBACK_PROVIDERS=akshare
```

本地 `.env` 已由 Git 忽略；`.env.example` 只提供占位值。

## 只读 CLI 与健康检查

```powershell
python -m backend.data_sources.cli industry-health
python -m backend.data_sources.cli industry-preview --provider auto --level 3 --limit 20
python -m backend.data_sources.cli symbol-industry --provider tushare --symbol 600519.SH
```

命令默认不写数据库，输出实际 Provider、是否降级及少量预览。现有健康服务新增：

```text
GET /api/data-source-health/unified
```

旧的板块来源健康接口保持兼容。

## 测试

自动测试必须离线，通过伪造 SDK/DataFrame 验证字段映射、树构建、唯一性、错误分类、超时、Token 脱敏、主备行为、注册入口和 API。真实接口测试仅由开发者本地手动执行上述 CLI。

## 已知限制与后续迁移

- 本 PR 不持久化申万行业主数据；PR5.11 再实现正式同步。
- AKShare 三级成分当前因上游字段变化而降级，需独立适配或等待上游修复。
- Tushare 全量成员可能触发大量三级分批请求，真实耗时取决于权限和限频。
- 2026-08-01 真实只读验证：Tushare 认证及 `index_member_all` 权限正常；三级目录返回 336 个节点，完整分批约 152 秒；`600519.SH` 正确返回“食品饮料 / 白酒Ⅱ / 白酒Ⅲ”。
- 同次验证发现 `002141.SZ` 在 Tushare 返回两个 `is_new=Y` 且 `out_date` 为空的三级归属。目录能力与成员唯一性已解耦：目录仍可用，全量成员正式结果严格失败并保留冲突，不推断最新一条为正确值。
- 旧 AKShare、GM、Tushare 业务调用尚未迁移；后续依次迁移行业主数据、行业快照、评分和首板行业评分。
