# SciMirror v0.2 实验协议

## 研究与解释边界

v0.2 检查在一组显式模拟假设下，奖励通路和合作候选学科组成是否改变 Agent 行为与模拟产出。mock 使用确定性生成器和 90 篇合成语料，只能验证软件、协议与模拟内部机制。它不测量现实科研质量，不能支持现实政策因果推断。

真实科研质量必须与模拟制度评分分开。`expected_*` 是决策前主观特征，`institutional_*` 是提交后的模拟制度反馈，外部盲评是实验结束后的独立评价；三者不得混用或反馈泄漏。

## 冻结设计

- 20 个 Agent，领域人数为 agents/learning/science = 7/7/6。
- 每 6 tick 一个完整周期；只允许在周期边界分叉。
- tick 0–5 是 balanced/open 共同前缀；默认干预为 tick 6–29。
- 三种 policy：balanced `(0.5,0.5)`、novelty `(1,0)`、recognition `(0,1)`。
- 两种网络：closed 与 open 都严格提供 3 个冻结候选；closed 为 3 名同领域，open 为 1 名同领域与 2 名其他领域。
- policy、network 或后续资源状态不得改变冻结候选表；不可行动槽位不补人。
- pressure 固定，dynamic values 关闭，避免同时引入未经校准的动态心理机制。

## Recognition 与 novelty

主题词表固定在 `data/topics.json`。只使用 `year < cutoff_year` 的语料。多标签论文对每个主题贡献 `1/m`；以 `alpha=1` 平滑后计算：

```text
p_k = (C_k + alpha) / (N + alpha*K)
attention_k = p_k / max_j(p_j)
recognition(z) = mean(attention_k for k in fixed_classify(z))
```

`community_attention_proxy` 使用冻结分类器识别最终标题、假设与方法。无匹配时为 0 并保留空主题列表。`lexical_novelty_proxy` 仍是相对截点前语料的最大 Jaccard 相似度补数。二者不是代数互补，也不声称统计独立。

## 五条 policy 通路

每条决策审计包含候选、可见证据、个体效用、policy 项、成本、总效用、概率、随机流键和最终动作。

- topic：在固定主题候选中选择研究主题。
- retrieval：先按查询构成统一候选池，再以探索/关注特征重排。
- idea_selection：在两个通过 schema 校验的候选 Idea 中选择持久化草案。
- invitation：只在冻结 offered 列表中比较候选；接受冲突在共同快照上解析。
- exit：每人每周期至多一次 continue/exit 决策；关闭 exit policy 只移除政策项，不删除 exit 动作。

`all_policy_paths_off` 将五条通路及制度反馈固定为 balanced。提示上下文、语义缓存键和决策审计都只使用 effective policy；运行后按 seed×network 比较去除纯分支标签的终态哈希。

## 六阶段与项目状态

| phase | 行为 |
|---:|---|
| 0 | 选题、检索、生成两个候选、选择并持久化一个草案 |
| 1 | 为 20 个草案建立 20 个初始项目；负责人从冻结列表邀请 |
| 2 | 接受/拒绝；被接受者的个人项目标为 merged 并链接到目标项目 |
| 3 | 团队共享；并发生成退出意图后统一解析，必要时中性接任或 abandoned |
| 4 | 所有存续项目修订；revision 只新增版本，不新增草案 |
| 5 | 提交、固定规则评分、completed；周期边界仍 active 才能记为 censored |

投入账本不删除、不复制。proposal 成本属于初始个人项目；合并只链接去向；分享、修订、退出切换和提交按发生项目记录。合并链上的投入映射到最终存续项目。完成成果中 Agent 的份额是其正投入除以该成果总正投入，每个成果份额和为 1。

## 配置与运行

- `configs/v02_mock_smoke.json`：3 seed、12 tick。
- `configs/v02_mock_full.json`：3 seed、30 tick，主实验 18 分支。
- `configs/v02_mock_diagnostic.json`：预先固定 seed 100–109、30 tick。
- `configs/v02_llm_pilot.json`：真实兼容后端试跑配置；默认只估算，不执行。
- 消融：full、no_topic、no_retrieval、no_invitation、no_exit_policy、all_policy_paths_off。

Linux 是权威运行平台，Python 必须不低于 3.11。`execute_v02.py --suite` 创建环境后依次运行全部测试和离线套件。每次输出使用新目录，程序拒绝覆盖事件日志。

## 输出与统计单位

主表区分候选 Idea、选定草案、启动/合并/废弃/完成/截尾项目、最终成果、solo/team 成果、总投入与每 100 投入成果。另报告主题熵、邀请接受/冲突/缺额、退出、跨领域历史贡献者和完成时团队口径。

配对效应以世界 seed 为独立单位：同 network 下 policy 相对 balanced，同 policy 下 open 相对 closed，并计算 policy×network 差中之差。bootstrap 只重采样世界，不把 Agent、Idea 或 tick 当独立重复。3 seed 和 10 seed 均是探索性诊断，不以区间是否跨零作为软件验收标准。

每次归档包含 config、manifest、status、usage、topic/candidate/decision 审计、项目与投入账本、Agent 份额、盲评表、逐分支事件/检查点/终态、分支和总重放验收。盲评表不含 policy、network、奖励或主观分数，私有映射不得交给评审者。
