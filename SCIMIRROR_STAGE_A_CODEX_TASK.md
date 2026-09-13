下面是可直接交给 VS Code 中 Codex 的执行文档。当前文件环境连接失败，无法生成下载文件；你可以将以下内容保存到项目根目录，命名为 **`SCIMIRROR_STAGE_A_CODEX_TASK.md`**。

这次只完成阶段A：**检索诊断、针对性修复与冻结状态实验**，不扩展盲评或重新设计科研社会引擎。

------

# SciMirror 阶段A：冻结状态检索实验执行文档

## 0. 任务目标与执行范围

在当前 SciMirror v0.3 仓库上完成以下工作：

1. 复现并定位选题、记忆、领域偏好与检索结果之间的问题。
2. 对实际问题做最小必要修复。
3. 建立可重复、无状态污染的冻结状态检索实验。
4. 完成648次核心检索实验、定向测试和结果分析。
5. 交付代码、配置、实验数据、验收文件和报告。

本文是执行规格，不是已实现功能声明。请先检查仓库，再按实际文件映射实现，不要只返回计划。

### 执行边界

- 保留用户已有修改、旧入口、旧实验结果和模型配置。
- 旧归档只读，所有新实验写入独立目录。
- 默认使用现有合成语料，不下载真实文献、不调用LLM或收费API、不安装大模型。
- 不修改奖励定义、邀请、项目合并、退出、贡献账本或盲评模块。
- 不强制让 novelty 政策获得更高分，不以显著性作为验收条件。
- 不要求每次换主题、记忆或policy都必须更换文献。
- 完成后不得声称已经验证真实文献检索质量或现实科研政策效应。

## 1. 已知问题与证据边界

上一轮 v0.3 主实验有以下现象：

| 现象                   | 已知证据                                      | 本次需回答                         |
| ---------------------- | --------------------------------------------- | ---------------------------------- |
| 记忆相关性不起区分作用 | 1440条记录的候选文献 memory 相关性分量全部为0 | 表示、分词、数据传递还是设计导致？ |
| 相关性门槛未筛除候选   | minimum_relevance=0.08，候选全部合格          | 门槛是否被领域基础分抵消？         |
| 部分跨领域选题匹配失败 | 68次检索的所选文献均不匹配选题标签            | 目标文献未召回，还是重排序压制？   |
| 去重效果证据不足       | 当前检索近重复率为0，但存在模板变体           | 检测是否能识别已知近重复？         |
| 冻结探针覆盖有限       | 仅少量主题与policy比较                        | 能否推广到全部主题和不同状态？     |

这些是问题线索，不是预先确定的代码根因。

如果当前源码已经修复其中某项，验证现有实现并保留，不要回退后重新修改。

## 2. 开始前的仓库检查

读取：

- `AGENTS.md`
- `README.md`
- 当前检索与实验配置
- corpus、topics、query、ranker、cache相关实现
- Agent知识、记忆与阅读历史字段
- v0.3执行入口、测试和验收逻辑

先执行必要的已有测试，记录：

```text
当前commit
源码hash
Python版本
依赖版本
未提交修改
现有测试通过/失败情况
语料hash
主题定义hash
检索配置hash
```

生成 `STAGE_A_PLAN.md`，写明：

1. 实际模块与本文职责的映射。
2. 当前问题是否能够复现。
3. 计划修改的最小范围。
4. 基线和修复版如何共存。

随后继续实现，不必等待再次确认。

## 3. 保留可验证的旧版基线

### 3.1 两种核心实验模式

核心实验比较：

- `baseline_v03`：开始本任务时，仓库实际使用的检索实现。
- `stage_a_fixed`：本次修复后的实现。

不要把 `baseline_v03` 命名为 `legacy_v02`。二者不是同一版本。

若已有可运行的 v0.2 检索模式，可以作为额外历史对照，但不计入648次核心实验。

### 3.2 保留方式

优先复用现有策略接口，保留旧检索策略及其配置；避免复制整个项目。

基线必须：

- 对应实际源码版本；
- 保留真实旧行为，包括可能有问题的行为；
- 使用和修复版相同的输入语料、主题定义、状态快照；
- 输出统一的诊断schema。

若旧实现无法恢复，明确记录 `baseline_unavailable`，不要临时编写一个“看起来像旧版”的算法代替。

## 4. 冻结状态协议

### 4.1 冻结状态的含义

每次检索应是对固定输入的纯计算：

```python
result = retrieve(frozen_state, corpus_snapshot, retrieval_config)
```

检索不能：

- 追加Agent阅读历史；
- 更新记忆、声誉、能量、信任或项目；
- 更新attention统计；
- 消耗模拟引擎随机流；
- 将上一次探针的结果写入下一次探针的输入。

生产引擎可以在检索成功后更新阅读状态，但更新必须位于实验所调用的纯检索函数之外。

### 4.2 状态内容

至少保存以下信息，字段名可映射到现有实现：

```text
case_id
selected_topic_id
agent_field
semantic_memory
read_paper_ids
public_topics
其他确实参与检索的Agent特征
corpus_hash
topics_hash
cutoff_year
state_schema_version
```

policy与ranker模式作为处理变量独立保存。

`state_hash`不包含policy或ranker名称，以便检查跨处理输入是否一致。

运行前后计算状态hash：

```python
assert hash_before == hash_after
```

运行时应使用显式校验并抛出异常，不只依赖可被 `python -O` 禁用的 `assert`。

### 4.3 区分两种记忆

明确区分：

1. **语义记忆**：用于查询相关性的主题、关键词或研究摘要。
2. **阅读历史**：用于已读过滤或探索分数的文献ID。

不能同时改变二者，然后把结果解释为“语义记忆的作用”。

核心648次实验中：

- 只操纵语义记忆；
- 阅读历史固定为同一个空集合；
- 阅读历史对探索分数的影响另做定向测试。

## 5. 核心648次实验

### 5.1 因子设计

| 因子      | 条件数 | 操作                           |
| --------- | ------ | ------------------------------ |
| 选题      | 12     | 冻结主题表中的12个主题         |
| Agent领域 | 3      | 当前三个领域                   |
| 语义记忆  | 3      | 空、相关、不相关               |
| policy    | 3      | balanced、novelty、recognition |
| 检索模式  | 2      | baseline_v03、stage_a_fixed    |

总计：

12×3×3×3×2=64812\times3\times3\times3\times2=648

共有108个基础状态，每个状态运行6个处理组合。

这648次是**检索调用**，不是648个独立随机世界，也不涉及20 Agent社会模拟循环。

如果当前主题或领域数量与上述不同，记录实际情况和实验数量，不伪造12个主题或3个领域。

### 5.2 记忆构造

为每个主题预先固定：

- `empty`：空记忆。
- `related`：与当前主题有关的记忆。
- `unrelated`：来自预先指定其他主题的记忆。

相关和不相关记忆尽量保持相同条目数和近似长度，避免记忆量成为主要区别。

保存映射：

```text
topic_id
memory_condition
memory_content
source_topic_ids
construction_rule
```

不得根据检索结果临时选择“最容易产生差异”的不相关记忆。

### 5.3 配对比较

分别输出：

- 相同状态、policy下：baseline对fixed。
- 相同状态、ranker下：不同policy。
- 相同主题、领域、policy、ranker下：不同记忆。
- 相同领域、记忆内容、policy、ranker下：只改变主题。

最后一项必须使用额外的主题配对探针。核心矩阵中的“相关记忆”随主题变化，不能直接当作只改变主题的对照。

## 6. 查询表示与记忆修复

### 6.1 统一规范化

复用或新增统一的文本规范化函数，用于：

- 主题描述和关键词；
- Agent语义记忆；
- 文献标题和摘要；
- 实际参与匹配的其他文本。

重点检查：

```text
agent_memory
agent memory
主题description
主题keywords
```

是否被转换为可比较的表示。

不能只展开query中的主题ID，却让memory仍以不可匹配的下划线ID参与词法匹配。

保存规范化前后的文本及token摘要，便于定位。

### 6.2 记忆分量验收

在具有明确词汇区分的fixture中：

- 空记忆不产生伪相关性；
- 相关记忆对相关文献产生非零且可解释的memory分量；
- 不相关记忆不应因通用停用词获得相同高分；
- 输入记忆确实进入排序计算和缓存键。

只要求定向案例中的分量和必要排序响应正确，不要求实际语料中所有memory分量都非零。

## 7. 相关性召回、门槛与排序

### 7.1 分离三个步骤

明确区分：

1. 基础召回；
2. 相关性资格判断；
3. 合格集合内的政策重排序。

输出每一步的文献ID，避免只保存最终top-k而无法定位丢失位置。

### 7.2 相关性门槛

将“与当前研究问题的相关性”与“领域偏好”分开。

建议采用：

```text
evidence_relevance：当前主题/研究问题与文献内容的相关性
context_preference：领域、记忆等上下文偏好
policy_preference：探索与关注度偏好
```

资格判断主要依据 `evidence_relevance`。

领域相同、attention高或记忆相似，不能单独使与当前问题无关的文献通过门槛。

若沿用其他合理设计，必须给出同领域无关文献无法仅靠基础分通过的验证证据。

### 7.3 召回范围

固定query下，各policy共用相同基础候选池和候选预算。

检查是否存在硬性领域过滤：

- 若保留，明确其研究含义与跨领域召回限制。
- 若修改为全语料召回加领域偏好，设置显式版本化配置。
- 将召回范围变化单独诊断，不能全部归因于分词修复。

核心配置保持候选池上限20、top-k上限3。

### 7.4 候选不足

当合格且去重后文献不足3篇：

- 返回实际数量；
- 保存shortfall原因；
- 不用无关文献或重复文献补满；
- 全部不合格时返回空结果及明确状态；
- 在现有引擎适配层检查空结果处理。

不能将空结果缓存成错误的“成功返回3篇”。

### 7.5 校准与测试隔离

先建立独立校准fixture，再冻结门槛、分数权重和去重阈值。

校准集与测试集：

- 按基础文献或模板家族分组划分；
- 同一文本的轻微变体不能跨集合；
- 保存划分清单及hash；
- 测试集不用于选参数。

如果查看测试结果后修改参数，记录为新一轮开发，不能继续把原测试集称为未见测试集。

## 8. 近重复与缓存

### 8.1 文献去重

区分：

- 相同ID/DOI；
- 相同规范化正文；
- 近重复正文；
- 同主题但方法或证据不同的文献。

定向样例至少包含：

1. 同文不同ID；
2. 仅scenario编号不同；
3. 仅标点、空白不同；
4. 表述相近但方法不同；
5. 相同主题但研究问题不同。

不能只删除所有数字来处理变体：年份、样本量和实验参数可能有科学意义。合成fixture中的变体标记可专门处理，但必须记录规则。

去重应作用于文献内容，不能将“主题标签相同”作为重复标准。

### 8.2 缓存

按阶段区分缓存依赖：

| 缓存阶段 | 至少包含                                          |
| -------- | ------------------------------------------------- |
| 语料索引 | 语料、截点、规范化及索引版本                      |
| 基础召回 | query、影响召回的上下文、索引版本、召回配置       |
| 最终排序 | 基础池hash、policy、记忆/阅读状态、排序和去重配置 |

检查冷缓存、热缓存输出一致。

改变输入后允许结果相同，但不允许错误命中不包含该输入的缓存。

保存缓存命中状态和键摘要，不输出任何密钥。

## 9. 诊断指标

| 指标                     | 定义与解释                             |
| ------------------------ | -------------------------------------- |
| selected_set_jaccard     | 两个所选文献集合的交集/并集            |
| intersection_over_min    | 交集大小/较小集合大小                  |
| topic_label_match_rate   | 所选文献中包含选题标签的比例           |
| relevance分布            | 候选和所选文献的原始相关性分布         |
| memory分布               | 空/相关/不相关记忆下的分量分布         |
| qualified_rate           | 资格通过文献数/实际被检查候选数        |
| selected_count           | 去重和门槛处理后的真实返回数           |
| near_duplicate_pair_rate | 所选文献对中近重复对占比               |
| unique_cluster_ratio     | 所选唯一簇数/所选文献数                |
| retrieval_shortfall      | 返回数量不足top-k的比例                |
| candidate_recall         | fixture中已知相关文献进入候选池的比例  |
| relevant_slot_precision  | 最终返回文献中已知相关文献的比例       |
| relevant_coverage_at_3   | 返回的相关文献数/min(3,可用相关文献数) |

无分母时返回null与原因，不将空集合记为完美匹配。

区分：

- 标签匹配；
- 排序器内部相关性；
- fixture人工定义的相关性真值。

三者不是同一指标。fixture真值只用于评估，不能传给排序器作为特征。

## 10. 必须完成的定向实验

| 编号 | 探针                | 必须验证                               |
| ---- | ------------------- | -------------------------------------- |
| A01  | 只换主题            | 在可区分fixture上优先返回对应证据      |
| A02  | 新版等义表达        | 预先定义的等义词与规范化规则按设计生效 |
| A03  | 只换语义记忆        | memory分量可解释、无状态污染           |
| A04  | 只换阅读历史        | 探索分量按设计变化，语义query保持固定  |
| A05  | 只换policy          | 基础候选池一致，政策分量按规则变化     |
| A06  | 同领域无关文献      | 不能只靠领域基础分通过门槛             |
| A07  | 跨领域相关文献      | 在允许的召回模式下能进入并被选中       |
| A08  | 高attention无关文献 | 不能绕过相关性资格判断                 |
| A09  | 全零/空证据         | 空返回和shortfall处理正确              |
| A10  | 合格证据不足        | 不补无关项、不重复填充                 |
| A11  | 近重复文献          | 识别指定变体，不误并不同研究           |
| A12  | 未来文献            | 不影响截点前索引、统计与结果           |
| A13  | 冷/热缓存           | 结果一致，输入变化正确失效             |
| A14  | 执行顺序打乱        | 每个case结果不因前一个case变化         |
| A15  | 重复运行            | 输入与配置相同则结果一致               |

fixture的预期应在测试文件中明确；不要把待测实现的输出当作测试期望。

对于词法系统不能处理的广义语义同义表达，报告能力边界，不假装已经实现语义检索。

## 11. 对已知问题进行针对性归因

至少在相关fixture上增加以下配对诊断：

1. 只修复memory表示，其他设置不变。
2. 只修改资格门槛逻辑，其他设置不变。
3. 只改变领域召回范围，其他设置不变。
4. 完整修复模式。

这些不必重复运行全部648组合，但必须明确输入、改动和结果。

如果模块耦合导致某项无法单独关闭，报告原因，不伪造单因素归因。

## 12. 代码组织建议

映射到现有模块，避免重复实现：

```text
retrieval/
    normalize.py
    query.py
    ranker.py
    dedup.py
    cache.py

experiments/
    frozen_retrieval.py
    retrieval_fixtures.py
    retrieval_metrics.py

tests/
    test_retrieval_memory.py
    test_retrieval_gate.py
    test_retrieval_dedup.py
    test_frozen_state.py
    test_retrieval_cache.py

configs/
    stage_a_frozen_retrieval.json

execute_stage_a.py
```

配置至少包括：

```json
{
  "schema_version": "stage_a_1",
  "rankers": ["baseline_v03", "stage_a_fixed"],
  "memory_conditions": ["empty", "related", "unrelated"],
  "policies": ["balanced", "novelty", "recognition"],
  "read_history_mode": "fixed_empty",
  "candidate_pool_size": 20,
  "top_k": 3,
  "fixture_seed": 4101,
  "execution_order_seed": 4102,
  "allow_network": false,
  "allow_llm_calls": false,
  "output_root": "outputs_stage_a"
}
```

这是配置字段示例。Codex必须根据实际仓库生成完整可执行配置，包括语料、主题、基线与修复模式参数。

## 13. 执行入口与恢复机制

实现以下命令，或提供对应兼容封装：

```bash
python3 execute_stage_a.py check
python3 execute_stage_a.py test
python3 execute_stage_a.py calibrate --config configs/stage_a_frozen_retrieval.json
python3 execute_stage_a.py run --config configs/stage_a_frozen_retrieval.json
python3 execute_stage_a.py analyze --run-dir <运行目录>
python3 execute_stage_a.py validate --run-dir <运行目录>
```

提供一键入口：

```bash
python3 execute_stage_a.py all --config configs/stage_a_frozen_retrieval.json
```

要求：

- 支持 `--help` 和 `--dry-run`；
- `all`覆盖检查、测试、校准、实验、分析和归档；
- 首次校准后冻结配置hash；
- 支持按case恢复，已完成且hash一致的case可复用；
- 输入或配置变化时使用新运行目录；
- 错误不得静默跳过，保存失败case和原因。

Windows激活对应Python环境后可使用 `python`；文档记录实际解释器路径。

## 14. 引擎接入检查

除纯检索实验外，完成一个小规模接口回归：

- 在现有mock环境中调用生产检索接口；
- 验证实际进入 `stage_a_fixed`，不是只在实验脚本中修复；
- 验证检索返回值能被现有Idea生成输入使用；
- 验证空结果和少于3篇的结果不会破坏状态；
- 保留调用配置和ranker版本证据。

不要求重跑全部30 tick社会实验。阶段A不将社会产出变化作为通过条件。

旧配置继续显式使用旧模式，新增配置选择新模式。不能静默更改历史配置含义。

## 15. 输出与报告

每次生成独立运行目录，至少包含：

```text
config.json
manifest.json
status.json
usage.json
calibration_manifest.json
frozen_states.jsonl
core_results.jsonl
candidate_scores.jsonl
paired_comparisons.csv
metrics_by_condition.csv
targeted_probe_results.jsonl
failure_cases.jsonl
STAGE_A_REPORT.md
DELIVERY_VALIDATION_STAGE_A.json
```

`manifest.json`记录：

- 基线与修复版源码hash；
- 语料、主题、fixture和配置hash；
- 规范化、召回、排序、去重与缓存版本；
- 核心case预期数和实际数；
- Python与依赖版本；
- 实际执行的网络/API请求数。

报告必须回答：

1. memory全零的原因是什么，如何验证？
2. 门槛此前为何全部通过？
3. 跨领域失败发生于召回还是排序？
4. 去重是否识别已知变体？
5. 哪些相同结果是合理行为？
6. 哪些结论只适用于合成fixture？
7. 当前是否具备接入下一阶段实验的条件？

648次确定性组合不按独立随机样本计算显著性。优先报告条件分布、配对差值与失败案例。

## 16. 验收标准

以下条件全部满足，才能声明“阶段A完成”：

- 基线来源真实且可追溯。
- 108个基础状态、648个核心case完整；若配置不同，数量明确解释。
- 每次调用前后状态hash一致。
- 冷/热缓存和执行顺序不会改变同case结果。
- memory定向测试通过。
- 同领域无关文献不能仅靠领域偏好过门槛。
- 跨领域相关fixture按配置正确处理。
- 空证据、数量不足、未来文献与近重复测试通过。
- 政策不改变固定query下的基础候选池。
- 指标从逐条日志重新计算一致。
- 生产接口实际接入修复模式。
- 网络与LLM调用均为0。
- 报告包含失败、限制与实际检查数量。

验收JSON分别记录：

```text
baseline
frozen_state
memory
relevance_gate
cross_field_retrieval
deduplication
cache
core_matrix
targeted_probes
engine_integration
offline_execution
```

每项包含 `status`、`checked_count`、`failed_count`、`evidence_path`。

不能用“日志非空”替代行为验收，不能用“报告已生成”替代实验完成。未检查项目标记 `not_checked`，不得自动记为通过。

## 17. 给Codex的启动指令

请读取 `SCIMIRROR_STAGE_A_CODEX_TASK.md`，在当前SciMirror v0.3仓库上完成阶段A。先检查已有修改和实际检索实现，保留真实基线与旧结果。定位并修复memory全零、相关性门槛、跨领域召回和近重复问题，完成648次冻结状态核心实验、定向测试及生产接口回归。默认不联网、不调用LLM、不修改科研社会机制。持续执行到代码、配置、实验、分析和验收全部完成，不要只返回计划；未解决项必须如实记录，不能强制产生预期结果。