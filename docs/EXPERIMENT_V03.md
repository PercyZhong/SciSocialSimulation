# SciMirror v0.3 实验协议

## 范围

v0.3 是 v0.2 的兼容升级：v0.2 状态机仍产生 20 Agent、30 tick、6 条件的模拟分支，v0.3 在其外增加版本化检索、独立评审和后分析层。旧配置、日志和结果不被重新解释或覆盖。mock 与合成语料只能验证工程和模拟内部机制，不能支持现实科研质量、团队效应或科研政策的因果结论。

## 检索协议

`relevance_gated` 将 `selected_topic` 解析为冻结 `topics.json` 中的 topic ID、描述和关键词，并明确记录 topic/field/memory 分量。基础候选池按原始相关性稳定排序、固定为 20；原始相关性不达门槛的文献不能进入重排序。policy 仅在同一合格池内重排 exploration 与 attention 特征。全零相关、合格数不足和近重复限额都可以导致少于 3 篇的结果，且必须记录 fallback；模拟器在少于两条证据时进入低证据等待路径，不虚构草案。

精确重复按 DOI（有时）或规范化文本识别；近重复使用冻结 CPU 词法 Jaccard 阈值和稳定 tie-break。近重复簇是审计工具，不等于主题相同，也存在连通分量链式合并风险。年份截点在语料加载阶段执行，所有条件共用语料和主题 attention 快照。

`legacy_v02` 可作为受控比较模式保留。版本比较应说明是否共享可序列化共同前缀；同 seed 本身不表示共同初态。

## 评审协议

评审总体是某一指定运行中干预期去重的 completed 项目，不能把 smoke、消融、main 和诊断混合为一个总体。每项保存文本/证据 hash、条件和历史正贡献者口径。默认 strata 为 `seed×policy×network×output_type(solo/team)`，每层简单随机不放回抽 `min(3, N_h)`，并记录 `N_h`、`n_h`、纳入概率和抽样随机种子。空层明确保留。

公开包仅含随机持久 review ID、标题、假设、方法、参考证据和 rubric；不含 seed、policy、network、项目 ID、Agent、团队、路径或制度评分。私有键、样本权重和 reviewer provenance 必须留在 `review_private/`。文本研究方向或风格仍可能使评审者猜测条件，不能承诺完全盲化。

四个评分维度为 novelty、feasibility、scientific_value、evidence_support，均为 1–5，要求理由与可定位证据。必要材料不可访问时使用 `unassessable` 和 null，不能填 0 或强迫给低分。外部评分不能读取 Agent 私有状态，也不能改变模拟或缓存。

人工导入拒绝越界分数、重复 reviewer-review 配对、未知 review ID/证据、错误 rubric 与 `is_mock=true`。mock review 只用于测试且显式 `validation_only=true`，永不进入正式质量结果。两位评分者才计算二次加权 Cohen kappa；恒定、无共同项或单评分者时报告 null 和原因。评分差至少 2 分进入分歧队列；裁决不能替换原始一致性数据。

## 数量和质量分析

项目流量必须满足 `started = merged + abandoned + completed + censored`，成果类型满足 `solo + team = final`，贡献份额逐项守恒。成员离队、发生离队的项目、全员离队导致废弃、其他废弃和失败投入单独报告。

跨条件的 `Δcompleted = Δstarted − Δmerged − Δabandoned − Δcensored` 仅是会计恒等式，不是中介因果分解。近重复仅在 branch 内报告，跨条件重复另作审计，不能从任一条件扣除别的条件的成果。

若每项有两位有效评分，质量公式为：

```text
Q[p] = mean_d((mean_valid_reviewer_score[p,d] - 1) / 4)
```

四维等权、`Q∈[0,1]` 是透明分析约定，不是科研价值的比率尺度。分层样本且每层评分完整时使用 Horvitz–Thompson 估计：`Σ_h N_h × mean(Q_h)`；分别报告已评样本和总体估计。评分非响应不由纳入概率自动修正，结果保持 incomplete，并给出 `Q∈[0,1]` 的缺失界限。世界 seed 是政策/网络配对的重复单位；世界 bootstrap 条件于当前样本，不能假装覆盖评审抽样不确定性。

评分文件允许按 reviewer 或批次增量导入。每次导入先校验全部新行，再追加原始记录并合并到累计有效评分；已导入的 `review_id×reviewer_id` 不得重复覆盖。状态依次为 `awaiting_external_reviews`、`reviews_partially_imported` 和 `reviews_imported_complete`。所有抽中项目的两份评分及四维分数完整前，配对质量均值与区间保持空值。

质量敏感性预先报告四维等权，以及分别强调 novelty、feasibility、scientific_value、evidence_support 的五套权重。逐 seed 条件表和 solo/team 表报告四维均值、质量总量与均值；solo/team 另报告按分支内部聚类后汇总的冗余率。政策、网络及二者交互的质量差异在 seed 层配对，并用配置中的固定随机种子和次数进行世界 bootstrap。

## 执行与状态

Linux/Python 3.11+ 是权威平台。所有 check/test/analyze/export 命令离线；默认无付费 API 调用。没有已授权的独立评审资源时，评审包状态必须为 `awaiting_external_reviews`，整体交付可标为 `completed_with_external_review_pending`，但不得声称真实质量、评审覆盖或一致性已经完成。只验收模拟运行且未提供诊断与评审包时，整体状态为 `completed_simulation_only`；部分评分为 `completed_with_external_review_incomplete`；全部评分已导入但存在不可评项目时保持质量不完整。
