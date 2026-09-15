# SciMirror 阶段 A 收尾：代码修改与实验执行文档

版本：1.0
日期：2026-09-14
目标仓库：`https://github.com/PercyZhong/SciSocialSimulation`
审阅基准：`ba45e7b42e0eb5655a3e17dfc6e0313eba4c9445`

## 1. 给 Codex 的执行指令

请直接在当前项目中完成本文件规定的代码修改、离线实验、验收、中文报告与可复现归档。

开始前阅读仓库 `AGENTS.md`，检查当前提交和工作区改动。如果当前代码比审阅基准更新，先核对本文件列出的问题是否仍存在，保留已有正确修复，并在报告中记录差异。

执行约束：

1. Linux 为正式实验平台，Python ≥3.11；Windows 可以通过 VS Code Remote SSH 或 WSL 操作 Linux。
2. 保留用户尚未提交的修改，不覆盖历史实验目录。
3. 本轮仅运行本地语料和 mock 后端，不调用网络检索、LLM 或收费 API。
4. 保留既有奖励公式、合作候选人数、退出规则和产出统计口径。
5. 不以提高 policy 差异为优化目标，不强制不同 policy 返回不同文献。
6. 不通过删除难例、改写 gold 或降低验收阈值，把失败改成通过。
7. 不自动提交、推送或合并远程分支。
8. 完成所有可执行步骤；验收失败时保留失败结果和归档，明确未通过原因。

交付目标是：**修复已知检索缺陷，同时证明评估器、生产调用链和复现包可信。**

## 2. 已核实的问题与修改边界

| 问题                                        | 已核实的源码位置                                  | 本轮操作                                           |
| ------------------------------------------- | ------------------------------------------------- | -------------------------------------------------- |
| 证据查询只展开关键词                        | `scimirror/v03_retrieval.py::build_stage_a_query` | 增加版本化检索语义描述                             |
| 单词重合即可贡献证据相关性，混入 field 文本 | `stage_a_component_relevance`                     | 将证据匹配限定到正文内容，区分通用方法词与主题证据 |
| 通用 `evaluation` 容易淹没 Idea evaluation  | `data/topics.json`、补充语料生成器                | 保留原始难例，增加语义正反例并修复匹配             |
| 部分验收只检查行数                          | `scimirror/stage_a_supplement.py::validate`       | 分离运行、数据完整性、指标正确性与质量门槛         |
| 0/1 条证据由测试脚本自行判断通过            | `stage_a_supplement.py::engine_integration`       | 改为调用生产 `step_v02` 并检查实际事件和状态       |
| 复现包只复制部分模块并打平路径              | `stage_a_supplement.py::package`                  | 保留包结构，补齐依赖，在独立目录实际重跑           |

上一轮诊断发现：扩充语料上总体 gold 容量覆盖率约为 90.74%，但 `science_evaluation` 约为 3.70%；每个 ranker 的 27 个相关状态中，有 24 个未选中 gold 相关证据。

这些数字是**待复算的历史参照**。本轮应从旧归档重新计算并记录差异，禁止把历史数字写成硬编码验收结果。

## 3. 文件改动计划

以下新路径是建议路径；若仓库已有等价模块，优先复用并说明映射。

| 文件                                     | 操作                                   |
| ---------------------------------------- | -------------------------------------- |
| `scimirror/v03_retrieval.py`             | 新增版本化检索实现，保留旧模式         |
| `scimirror/corpus.py`                    | 增加新检索模式的分派                   |
| `scimirror/stage_a_closure.py`           | 新增冻结实验、指标复算、分层验收与报告 |
| `execute_stage_a_closure.py`             | 新增一键执行入口                       |
| `configs/stage_a_closure.json`           | 新增实验配置                           |
| `data/retrieval_topic_profiles_v1.json`  | 新增检索专用主题描述                   |
| `data/stage_a_closure/`                  | 新增正反例、标注说明与冻结划分         |
| `tests/test_stage_a_closure.py`          | 新增检索、评估器及恢复测试             |
| `tests/test_engine_evidence_boundary.py` | 新增真实引擎边界测试                   |
| `docs/EXPERIMENT_STAGE_A_CLOSURE.md`     | 记录实验定义与解释范围                 |

必要时修改 `v02_engine.py` 的审计字段，但不得无理由重写低证据处理规则。

## 4. 任务一：针对性修复检索语义

### 4.1 保留可执行的旧检索基线

保留现有 `stage_a_fixed` 行为，新增：

```json
{
  "mode": "stage_a_semantic_guarded",
  "version": "stage_a_closure_1"
}
```

旧配置继续使用原有行为，新配置显式启用新模式。

基线应对应审阅提交中的实际实现及其依赖。不能把同一个已修改函数绑定成两个变量，然后声称完成旧版与新版比较。

记录：

- 基线提交和实际执行代码哈希；
- 新实现及依赖哈希；
- 输入语料、检索主题描述、配置哈希；
- recognition 快照哈希；
- 去重版本。

### 4.2 检索语义与 recognition 分类分离

保留 `data/topics.json` 和 `TopicModel` 当前分类、attention 计算行为。

新增 `retrieval_topic_profiles_v1.json`，专门定义检索语义，避免修改主题关键词时顺带改变 recognition。

建议结构：

```json
{
  "version": "retrieval_topic_profiles_v1",
  "topics": {
    "science_evaluation": {
      "definition": "Evaluation of scientific research ideas and proposals",
      "positive_phrases": [
        "research idea evaluation",
        "scientific idea assessment",
        "research proposal evaluation"
      ],
      "concept_groups": {
        "object": ["research idea", "scientific idea", "research proposal"],
        "assessment": ["evaluation", "assessment", "feasibility", "scientific value"]
      },
      "generic_terms": ["evaluation"],
      "scope_note": "General evaluation of a model or algorithm alone is insufficient."
    }
  }
}
```

这只是字段示例。实施时补齐 12 个主题，并依据已有主题定义检查边界。

特别注意：

- `agent_memory` 与 `science_retrieval` 允许共享证据。
- 不把任何出现 `retrieval` 的内容都认作 Agent memory。
- 不把任何出现 `evaluation` 的内容都认作 Idea evaluation。
- 主题描述不得包含文献 ID、gold family ID 或测试样本专用字符串。
- 如果语义边界无法自动确定，记录标注争议，不能伪装成人工已确认。

### 4.3 新检索流程

采用以下顺序：

1. 从冻结检索描述构造主题查询。
2. 在全部符合年份条件的本地文献上计算证据相关性。
3. 执行证据门槛。
4. 构造最多 20 篇的共同候选池。
5. 在合格池内计算 context 和 policy 重排。
6. 按冻结去重规则选取最多 3 篇，允许不足。

实现要求：

- 证据匹配只使用 `title` 和 `abstract`；领域字段可用于上下文重排。
- 采用透明的短语匹配、概念组共现和词项区分度方法。
- 通用词不能单独使文献通过证据门槛。
- 跨词概念匹配必须限定在同一句或冻结长度窗口，不能靠长摘要中任意远距离共现凑足条件。
- 标点、连字符、大小写和常见单复数应有一致处理。
- IDF 等统计量只从符合年份条件的生产语料计算，不读取 qrels。
- 禁止以 gold 标签、文献 ID 前缀或合成模板编号参与排序。
- 同一冻结状态下，三个 policy 的召回池和证据合格池必须一致。
- field、memory 和 policy 不能使证据不合格文献重新入选。
- 不因返回不足而回退到无关文献填满三篇。

首先实现一种透明词法方案。本轮不同时引入向量数据库、embedding 服务和多个重排模型。

### 4.4 参数校准与版本冻结

只在 calibration 数据上选择参数，记录尝试过的配置及选择理由。

正式评估前保存：

```text
FROZEN_PROTOCOL.json
calibration_trials.csv
retrieval_topic_profiles_v1.json
```

协议必须包含完整阈值、匹配窗口、字段权重、门槛规则及哈希。

旧扩充语料的失败主题已经被分析过，因此应称为**已知问题回归集**，不能重新宣称是未见测试集。

新增按文献 family 隔离的留出集合。由同一 Codex 设计和标注的合成集合仍只是工程留出集，不等同于独立人工 gold。

### 4.5 必须保存的诊断字段

每次检索保存：

```text
case_id
state_hash
corpus_hash
query_profile_hash
recognition_snapshot_hash
ranker_version
topic_id
field
memory_condition
policy
paper_id
title_match_score
abstract_match_score
matched_phrases
matched_concept_groups
generic_only_match
evidence_score
gate_passed
gate_reason
context_score
exploration_score
attention_score
policy_score
final_score
candidate_rank
selected_rank
```

另外保存逐阶段文献 ID：

```text
scored_ids
evidence_qualified_ids
base_candidate_ids
ranked_ids
selected_ids
```

注意区分“全部语料已扫描”与“相关证据进入前 20 候选池”，不能用前者宣称候选召回完美。

## 5. 任务二：补充正反例与冻结状态实验

### 5.1 保留旧语料

原始语料、上一轮扩充语料及其 qrels 保持不变。

修复效果必须首先在相同旧数据上比较。不能通过重写所有摘要、删除通用 `evaluation` 或重新分配 family，制造检索改善。

若发现旧 qrels 错误：

1. 保留旧标注结果。
2. 创建版本化修订及逐条理由。
3. 分别报告旧标注与修订标注下的指标。
4. 不把标注修订贡献归因于检索算法。

### 5.2 新增有限的语义挑战集

至少覆盖：

| 类型         | 示例要求                                            |
| ------------ | --------------------------------------------------- |
| 明确正例     | 评价科学 Idea 的可行性、科学价值或证据支持          |
| 通用词负例   | 模型性能 evaluation，但不评价科研 Idea              |
| 上下文负例   | 文中分别出现 idea 和 evaluation，但不是同一评价对象 |
| 同义表达正例 | research proposal assessment 等合理表达             |
| 跨领域正例   | agents/learning 文献实际研究科研 Idea 评价          |
| 共享证据     | 同时涉及 Agent memory 与科研检索                    |
| 近重复变体   | 正文实质相同、表述或编号不同                        |
| 无相关供给   | 应允许返回零篇                                      |
| 年份边界     | 截止年前、截止年、截止年后文献                      |

`Corpus` 当前采用 `year < cutoff_year`。本轮保留此定义，并测试边界。

挑战集限制在约 30–50 个实质不同文献 family；不要无限扩充合成 benchmark。

每条 qrel 记录相关等级、依据、标注来源和版本，允许一个 family 对多个主题相关。

### 5.3 主冻结矩阵

复用上一轮 108 个状态：

```text
12 topics × 3 fields × 3 semantic-memory conditions
```

E1：

```text
108 states
× 2 corpora：original / expanded
× 2 rankers：reference / semantic_guarded
× 3 policies
= 1296 次核心检索
```

保持：

- `top_k=3`；
- `candidate_pool_size=20`；
- E1 阅读历史沿用 `fixed_empty`；
- 原有三个 policy 权重不变；
- 相同 state 的随机输入与识别标识一致；
- 输入状态在每次检索前后哈希一致。

新旧查询构造与评分共同改变时，主结果应称为**检索修复包的总效果**，不能只归因于评分函数。

额外用小型组件消融区分查询描述与评分规则的作用。只在字段兼容时比较，明确列出调用量，不并入 E1 的 1296 次统计。

### 5.4 Policy 与主题对检查

复用原 E2 场景和历史设置：

```text
54 次启用调用 + 6 次关闭控制
```

分别报告：

- 特征是否变化；
- 分数是否变化；
- 有序列表是否变化；
- 无序集合是否变化。

路径关闭时应保持对应结果一致。不得要求 E1 中每个状态都出现 policy 集合差异。

E3 复用空 memory 状态，计算：

```text
66 topic pairs × 2 corpora × 2 rankers × 3 fields × 3 policies
= 2376 行比较
```

共享证据导致非零 Jaccard 可以是正确结果；不能把主题之间“全部零重合”设成优化目标。

## 6. 任务三：把验收拆成四层

新增验收结构：

```json
{
  "execution_status": "completed",
  "integrity_status": "passed",
  "metric_correctness_status": "passed",
  "retrieval_quality_status": "failed",
  "engine_integration_status": "passed",
  "reproduction_status": "passed",
  "overall_status": "completed_with_limitations",
  "all_passed": false,
  "known_limitations": []
}
```

### 6.1 运行完成

检查任务是否结束、是否有异常、是否有未完成 case。

### 6.2 数据完整

检查组合键、重复、缺失、状态哈希、版本与输入一致性。

1296 行齐全只属于这一层。

### 6.3 指标正确

依据原始 selected IDs、独立 family 映射和 qrels 重新计算指标，不直接信任已有汇总列。

设：

- GqG_q：该 query 的可用相关 gold families；
- SqS_q：所选文献对应的 gold families；
- k=3k=3。

至少计算：

CapacityCoverage@k=∣Sq∩Gq∣min⁡(k,∣Gq∣)\mathrm{CapacityCoverage@k} =\frac{|S_q\cap G_q|}{\min(k,|G_q|)}

当 ∣Gq∣=0|G_q|=0 时返回 `null` 并标明无供给。

另计算文献级 Precision、family Recall、前 20 候选池的相关 family 覆盖、重复对比例和零相关返回率。

空结果处理：

- 有 gold 供给但未返回：覆盖率、召回率为 0；
- Precision 无分母时为 `null`；
- 汇总时同时报告空结果数，不能只删除这些行；
- 有返回但全部无关，与主动返回空集合分开报告。

为评估器添加手工可算的独立小例子，覆盖空返回、重复、共享 family、部分相关及零供给。

### 6.4 检索质量

以下是**本轮预设工程门槛**，不是外部科学有效性的标准。

针对扩充语料的新 ranker：

| 指标                                             | 门槛  |
| ------------------------------------------------ | ----- |
| `science_evaluation` 平均容量覆盖率              | ≥0.80 |
| 该主题有至少 3 个相关 family 时的零相关返回 case | 0     |
| 12 主题宏平均容量覆盖率                          | ≥0.90 |
| 任一有足够相关供给主题的平均容量覆盖率           | ≥0.80 |
| 原本覆盖率 ≥0.80 的主题，相对基线下降            | ≤0.05 |
| 通用 `evaluation` 单词负例通过证据门槛           | 0     |
| Gold 隔离扰动后的生产排序变化                    | 0     |

同时公布逐主题 Precision、短缺率及全部失败 case，避免以广泛召回无关文献换取覆盖。

原始语料允许因供给不足产生短缺，不以“必须返回三篇”为门槛。

这些阈值须在正式评估前冻结。未通过则保留 `failed`，不得临时降低。

### 6.5 验收器自身必须经得住反例

必须测试：

- 文件行数齐全，但选中 ID 全部错误；
- 汇总数值被改动；
- 删除一个 case；
- 重复一个 case；
- 状态哈希不一致；
- 打乱/替换 qrels 后生产排序仍不变；
- 缺少复现依赖；
- 检索质量失败，但工程测试全部通过。

上述情况应进入正确的失败类别，不能统一输出 `all_passed=true`。

## 7. 任务四：验证真实生产引擎的 0/1/2/3 证据路径

### 7.1 复用已有生产行为

当前实际调用链：

```text
step_v02
→ Corpus.retrieve_v02
→ 检索模式分派
→ 低证据判断
→ proposal_observation
→ backend.generate
→ validate_response
→ 状态更新和事件
```

`step_v02` 已经在 `len(papers)<2` 时生成 `knowledge.retrieval.shortfall` 并跳过生成。

测试必须调用 `step_v02`。不得在测试脚本中自行执行相同的 if/else 后写入 `passed`。

### 7.2 两层边界测试

第一层：控制检索返回数量，调用真实 `step_v02`，验证控制流。

第二层：使用真实 `Corpus`、新检索器和本地边界语料，仍调用 `step_v02`，验证端到端接线。

`Corpus` 当前至少需要 3 篇符合年份条件的文献，因此 0/1/2 条“相关证据”场景应加入无关文献满足加载要求，不能绕过生产加载器。

使用记录调用的 mock backend。0/1 场景可对目标 Agent 的 `propose` 调用设置直接报错的 spy，证明未进入生成。

### 7.3 检查内容

| 返回证据 | 应检查的行为                                 |
| -------- | -------------------------------------------- |
| 0        | shortfall 事件；无 propose 调用；无目标草案  |
| 1        | 同上；不能复制一篇证据凑数                   |
| 2        | 实际传入两篇可见证据；通过响应校验并生成草案 |
| 3        | 实际传入三篇可见证据；通过响应校验并生成草案 |

所有情况还需检查：

- 输入世界对象未被原地修改；
- `validate_v02` 通过；
- 引用只能来自实际可见证据；
- 无虚假项目、无重复投入；
- 下一 phase 不为低证据 Agent 创建不存在草案对应的项目；
- 事件写入、快照恢复和回放一致。

能量核算要考虑 tick 开头的 `+8`、上限 100 和低证据分支的 `-4`，不能简单断言最终能量等于初始值减 4。

目前低证据尝试不形成项目投入。保留该口径，显式记录检索尝试和能量变化；不要创建虚假项目把失败检索塞进项目账本。

此外运行：

```text
20 Agents / seed 42 / balanced / open / 12 ticks
```

保存真实事件、状态、调用记录和不变量检查结果。它属于引擎集成验证，不用于宣称社会机制效应。

## 8. 任务五：独立目录可复现归档

### 8.1 保持真实包结构

归档结构至少包括：

```text
delivery/
  README_REPRODUCE.md
  CODE_CHANGE_REPORT.md
  FINAL_REPORT_ZH.md
  DELIVERY_VALIDATION.json
  FROZEN_PROTOCOL.json
  runtime_manifest.json
  config.json
  metrics/
  diagnostics/
  engine_boundary/
  engine_smoke/
  reproduction/
    source/
      scimirror/
      tests/
      configs/
      data/
      execute_stage_a_closure.py
      run.py
      requirements.txt
      AGENTS.md
    CHECKSUMS.sha256
    REPRODUCTION_RESULT.json
```

根据实际 import 补齐依赖，不限于上表。不要把 `scimirror` 模块打平复制。

归档排除 `.venv`、API 密钥、缓存、历史输出和嵌套 ZIP。

### 8.2 实际隔离重跑

在新的临时目录解压复现内容：

1. 清除对原工作区的 `PYTHONPATH` 依赖。
2. 使用新的输出目录。
3. 检查模块实际加载路径位于解压目录。
4. 验证每个 checksum。
5. 运行核心冻结矩阵和关键引擎边界测试。
6. 比较语义结果。

比较规则：

- selected IDs、case keys、质量指标和关键事件必须一致；
- 时间戳、运行目录等环境字段不要求字节一致；
- 浮点字段使用预先声明的容差，例如 `1e-12`；
- 不将归档复制后的 SHA 一致解释为跨平台独立运行一致。

记录 Linux 平台、Python 版本、命令、退出码、实际源码哈希及依赖版本。

若代码存在未提交改动，同时记录工作区源码哈希或补丁，不能仅记一个并不包含修改的 commit。

## 9. 一键入口与执行顺序

新增入口支持：

```text
check
calibrate
freeze
run
analyze
validate
reproduce
package
all
```

其中：

- `check`：环境、输入和配置检查；
- `calibrate`：仅运行 calibration；
- `freeze`：写入协议和哈希；
- `run`：要求协议已冻结，运行正式离线评估；
- `analyze`：从原始结果复算；
- `validate`：按四层验收；
- `reproduce`：独立目录验证；
- `package`：即使质量未过门槛也能归档失败证据；
- `all`：按顺序完成全流程，不自动反复调参。

建议调用方式：

```bash
python3 execute_stage_a_closure.py all \
  --config configs/stage_a_closure.json \
  --output outputs_stage_a_closure/closure_001
```

此命令是本轮需要实现的新入口，不应假装当前仓库已经支持。

执行顺序：

1. 检查工作区与基线。
2. 复算上一轮已知问题。
3. 实现检索及诊断字段。
4. 在 calibration 上有限校准。
5. 冻结协议。
6. 运行 E1、E2、E3、挑战集和引擎验证。
7. 复算指标、运行验收。
8. 完成仓库要求的回归检查。
9. 独立目录复现。
10. 生成中文报告和最终归档。

遵守 `AGENTS.md` 的必要检查：

```bash
python3 --version
python3 run.py doctor --config configs/mock.json
python3 run.py test
python3 run.py run --config configs/mock.json
```

最后一项需检查 `completed`、18 行 summary、18 个最终状态各 20 Agents/tick 30、同 seed 分叉哈希一致及事件回放成功。

不需要额外启动未经本文件要求的大规模模拟。

## 10. 恢复、缓存与退出码

恢复键必须包含：

```text
input content hashes
state hash
ranker and dependency hashes
query profile hash
recognition snapshot hash
config hash
protocol version
```

要求：

- 相同协议下第二次执行复用已完成 E1 case；
- 核心结果不得重复；
- 输入或实现变化时拒绝复用旧结果；
- 不把绝对工作目录作为实验语义身份；
- 允许在独立解压目录重新运行；
- 分开统计实际计算、缓存读取、比较行数和测试调用；
- 不再将已知固定数值直接写成“实际调用量”。

建议退出码：

| 退出码 | 含义                               |
| ------ | ---------------------------------- |
| 0      | 所有必需验收通过                   |
| 2      | 已完成，但质量或其他必需门槛未通过 |
| 1      | 执行异常、输入损坏或任务不完整     |

退出码 2 前仍需保存报告及失败归档。

## 11. 最终中文报告必须回答的问题

1. `science_evaluation` 的故障主要发生在查询、证据门槛、候选截断还是重排？
2. 修复后在相同旧语料上的覆盖、Precision 和零相关返回率如何变化？
3. `agent_memory` 的不足来自真实检索错误还是 gold 边界争议？
4. 哪些主题改善，哪些主题退化？
5. 通用词负例、同义正例和共享证据分别表现如何？
6. Policy 改变了分数、顺序还是集合？关闭路径是否保持一致？
7. 0/1 条证据是否实际穿过生产引擎验证？
8. 质量失败是否会阻止 `all_passed=true`？
9. 独立目录是否确实重跑成功？
10. 是否具备进入真实文献小规模试验的工程条件？

报告应同时列出分子、分母和逐主题结果。108 个冻结状态不是 108 个独立世界；不能用这些确定性诊断行推断现实政策因果效应。

## 12. 停止条件与下一阶段

本轮通过条件：

- 已知检索主题失效得到修复并满足冻结门槛；
- 无明显跨主题退化；
- 评估器能识别错误输出；
- 生产低证据路径通过实际调用链测试；
- 独立目录复现成功；
- 所有必需回归检查通过。

达到后停止继续扩充合成测试，下一阶段转入：

**小规模真实文献语料 + 人工核验的检索相关性标注 + 独立 Idea 评价流程准备。**

若本轮未通过，交付定位清楚的失败清单。不要自动进入循环调参，也不要声称已经验证真实科研创新机制。

## 13. Codex 最终交付

请向用户交付：

1. 修改文件及行为变化说明；
2. 实际运行命令与测试结果；
3. 新旧逐主题检索指标；
4. 已通过和未通过的质量门槛；
5. 真实引擎边界测试证据；
6. 独立目录复现结果；
7. 最终归档的完整路径；
8. 是否建议进入真实文献阶段及理由。

最终必须明确区分：**工程完成、检索质量达标、科学有效性尚待验证。**

