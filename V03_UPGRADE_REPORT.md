# SciMirror v0.3 升级报告

日期：2026-09-11  
本地工程环境：Windows / Python 3.12.7  
权威运行环境：Linux / Python 3.11+（尚待服务器复验）

## 交付结论

三项 v0.3 升级的本地工程部分已完成：检索诊断与修复、可执行的独立盲评工作流、数量/质量联合计量。全部运行使用 deterministic mock 与 90 篇合成语料；HTTP 尝试为 0，未调用收费 API，也没有真实模型或人工评分。

独立评分和质量结论尚未完成，状态为 `awaiting_external_reviews`。待评包、导入验证、覆盖、一致性、分歧、抽样加权和缺失界限均已实现并导出，但没有把空模板或 mock 评分冒充为外部评审。

## 1. 检索弱联系：定位与修复

### 发现

冻结探针复现了旧路径的弱联系风险：topic ID 中的下划线作为单一词元，且宽泛 field/title 词在 tie-break 中占主导，因此不同主题可能收到相同文献。自然轨迹中同文献不必然是错误，但旧实现没有足够审计字段来区分“真实共同证据”和“查询未生效”。

### 实现

- `scimirror/v03_retrieval.py` 展开 topic ID 的冻结描述和关键词，并保存 topic/field/memory 查询分量、原始/归一化相关性、exploration、attention、政策分数、最终分数、合格状态、重复簇和 fallback。
- policy 只能重排同一个相关性合格池；不改变候选预算。全零或不足证据不补无关/重复文献。
- `scimirror/topics.py` 对语料预处理按论文 field 限制主题词规则，避免 “retrieval” 之类跨领域词制造错误的语料 topic 标签；最终成果文本仍可进行开放多主题识别。
- `retrieval_query_probes.jsonl`、`retrieval_diagnostics.csv`、`live_retrieval_audit.jsonl` 与 `retrieval_before_after.md` 提供冻结和实际轨迹审计。

### 结果与边界

冻结合成 fixture 中，`agent_memory` 与 `agent_tools` 只换 topic 的 top-3 文献 Jaccard 为 0；等义词序的 legacy 查询保持稳定。主实验 1,440 条实时检索记录中，平均 topic match rate 为 0.953，平均所选原始相关性为 0.699，无检索短缺。866 个仍有相同 query 的跨 policy 配对，其平均文献 Jaccard 为约 0.736；这是如实保留的稳定性，而非需要人为消除的错误。

这些分数只诊断冻结词法排序器对合成语料的内部行为，不是独立的科学文献相关性评估。

## 2. 独立盲评：本地流程完成，外部评分待完成

### 实现

- `scimirror/v03_review.py` 冻结 completed 项目总体，按 `seed×policy×network×solo/team` 分层 SRSWOR 抽样，记录 `N_h`、`n_h`、π、样本 hash 和独立 sample seed。
- 公共材料和私有条件映射严格分目录，随机 review ID 使用持久随机 token，而不是项目 ID 的可逆 hash；公共内容会扫描条件标识和制度分数字段。
- 导出的 rubric 覆盖 novelty、feasibility、scientific value 与 evidence support 四维 1–5 评分、理由、证据定位和 `unassessable`。
- 人工导入拒绝越界/重复/未知证据/错误 rubric/mock 记录；支持 coverage、二次加权 kappa、绝对分歧和≥2分分歧队列。mock reviewer 仅能生成 `is_mock=true, validation_only=true` 的测试数据。

### 当前待评包

使用唯一的 10-seed diagnostic 运行作为总体：3,057 个干预期 completed 项目，不与 smoke/main/消融混合。120 个 strata 均非空，每层抽 3 项，得到 360 个盲评材料、720 份双评模板。包的验证通过：随机 ID 唯一、公开/私有 ID 一致、π 正确、模板均非 mock，公开文件未发现条件标识或制度评分。

没有获得外部人工评审者或付费模型预算授权，因此 `review_results` 是带 schema 的空表，`status.json` 为 `awaiting_external_reviews`。费用估计为 null（没有授权 provider/price 配置），网络请求数为 0。

## 3. 数量、质量和投入：实现与当前结果

- `scimirror/v03_analysis.py` 保留项目守恒，分开合并、成员离队、全员离队废弃、其他废弃、完成、截尾和失败投入；并输出 relative-to-balanced 会计恒等分解，明确不是因果中介。
- branch 内完全/近重复采用规范化文本和冻结词法 Jaccard 连通分量；报告簇大小、重复对和 redundant output rate。主题相同但方法不同的定向 fixture 不会被高阈值规则合并。
- 已实现四维等权 `Q`、全量和分层 Horvitz–Thompson 估计、观察样本和总体估计区分、质量/投入比、solo/team 分开表及缺失 [0,1] 界限。没有有效双评分时，估计保持 null。

3-seed 主实验共计 1,440 个启动项目、420 个合并、80 个废弃、940 个完成；196 次成员离队，192 个发生离队的项目，80 个全员离队废弃，其他废弃为 0，失败投入为 2,088 模拟单位。各分支重复成果率平均约 0.636，近重复对率平均约 0.046；这是 mock 模板产生相近文本的诊断，不能当作真实科研重复率。

## 实验、测试和验收

| 归档 | seed | 条件分支 | tick | summary | trajectories | 状态 |
|---|---:|---:|---:|---:|---:|---|
| smoke | 3 | 18 | 12 | 18 | 18 | 完成、重放通过 |
| main/full | 3 | 18 | 30 | 18 | 72 | 完成、重放通过 |
| no_topic | 3 | 18 | 30 | 18 | 72 | 完成、重放通过 |
| no_retrieval | 3 | 18 | 30 | 18 | 72 | 完成、重放通过 |
| diagnostic | 10 | 60 | 30 | 60 | 240 | 完成、重放通过；用于评审总体 |

全套回归与新增定向测试共 38/38 通过。测试覆盖 topic 展开、可区分主题、零相关短缺、年份过滤、近重复、schema/cache 隔离、分层抽样、公开包泄漏、评分导入、一致性、质量估计/缺失界限、项目流量与既有 v0.1/v0.2 行为。

## 输出位置

- 检索诊断：`outputs_v03/delivery_20260911/retrieval_diagnosis/`
- smoke：`outputs_v03/delivery_20260911/smoke/`
- main/full：`outputs_v03/delivery_20260911/main/`
- 两组消融：`outputs_v03/delivery_20260911/ablations/`
- 10-seed 诊断、数量与质量空表：`outputs_v03/delivery_20260911/diagnostic_10seed/`
- 待评包：`outputs_v03/delivery_20260911/review_package/`
- 最终状态验收：`outputs_v03/delivery_20260911/DELIVERY_VALIDATION_V03.json`

## 未完成与解释限制

- 尚未在用户远程 Linux 环境执行，不能把本机 Windows 结果称为权威 Linux 验收。
- 尚未导入真实独立评分，因而没有可报告的 Q、质量调整成果、质量/投入率、评审一致性或质量配对效应。
- 评审抽样的纳入概率不能自动修正评分非响应；评分完成前仅报告缺失状态与边界。
- 20 Agent mock、合成语料、固定词法规则和模板化文本只验证平台协议；任何 policy、network、solo/team 差异都不能外推为现实科研规律或因果效应。
