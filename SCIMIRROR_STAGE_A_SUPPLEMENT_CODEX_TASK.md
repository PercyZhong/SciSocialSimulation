# SciMirror 阶段A补充实验：证据多样性、政策选择链路与证据簇评价

交付对象：VS Code 中的 Codex。编制日期：2026-09-14。

本文是代码修改和实验执行规格，不是已实现功能声明。请在用户现有 SciMirror 仓库上持续执行，完成实现、测试、离线实验、分析与归档；不要只返回计划。

## 0. 本轮目标与范围

阶段A已经完成冻结状态、memory表示、相关性资格判断、跨领域召回和去重的基本工程验证。本轮只补足三个问题：

1. 增加内容不同的合成证据，让检索有足够独立候选，同时保留重复和不足案例。
2. 检验固定候选池内的policy效用是否真实传递到排序及选择，区分“机制无效”和“没有选择空间”。
3. 增加基于独立标注的证据簇指标，检查合理跨主题重合、误合并和证据不足，并接入小规模mock回归。

不重新设计奖励、合作、项目退出、贡献账本或盲评。不执行真实模型评分、不抓取真实文献、不调用LLM/收费API，不要求GPU。真实语料迁移仅作为后续接口和待办记录，不是本轮完成条件。

保留已有代码修改、阶段A结果、旧配置和旧入口。新输出使用 outputs_stage_a_supplement/<run_id>。执行规格中的模块路径允许映射到已有职责，禁止为了符合文件名创建重复引擎。

## 1. 已知证据与解释边界

上一轮输出：108个基础状态、648次核心调用；stage_a_fixed的324次调用全部shortfall，平均返回1.1667篇；多数主题仅1个可用证据簇。修复版108个固定状态中三个policy所选集合全部相同。

这不证明policy失效：当合格簇数小于top_k，三个policy可能只能选择同一集合；阅读历史固定为空时探索特征也可能缺少差异。

旧coverage@3按文献条目计分，去重后从0.8611变成0.3333，混合了检索覆盖与模板去重影响。主题标签匹配不是独立科学相关性判断。

memory和retrieval两个主题在核心结果中返回同样两篇、顺序相反。旧topic-pair探针只覆盖部分主题对，不能把探针Jaccard=0推广为全部主题对。

## 2. 开始前的检查与版本冻结

读取AGENTS.md、阶段A执行文档、实际retrieval/query/dedup/cache、冻结实验器、指标实现、生产适配层与测试。

输出 SUPPLEMENT_PLAN.md：实际模块映射、已有功能、最小修改范围、原有失败、版本和数据准备策略。之后继续实现。

将当前阶段A修复版冻结为 stage_a_reference；本次新增版称 stage_a_supplement。两者共用真实输入和可比较输出，reference不受新工具函数的无意修改。优先已有策略接口或隔离适配器，不复制整个仓库。

记录commit、未提交补丁hash、reference函数及依赖hash、新版hash、Python、依赖、原语料/主题/配置hash。若本轮仅增加实验和指标、没有必要修改排序器，允许两个ranker行为完全一致，报告这一事实，不人为制造算法改进。

不能恢复当前基线时明确标记baseline_unavailable，完成其余可做工作；不得猜写旧实现后声称真实对照。

## 3. 合成证据扩充：先定义内容，再定义评估标签

### 3.1 两套冻结语料

- corpus_original：保留阶段A实际输入，只读。
- corpus_expanded：新建的、多样化合成证据集，不覆盖demo_papers.jsonl。

默认对现有12主题，每个主题至少准备6个独立基础证据家族：共至少72个主题—家族关联。共享家族可以关联多个主题，但必须报告唯一家族总数，不能将共享文献复制成独立证据。

建议大部分家族只归属于一个主题，另加入明确的memory/retrieval等共享家族。不要仅靠不同topic_id、标题、数字或改写来达到6个家族。

每个独立家族必须在下列方面至少有两项实质不同，且至少一项是研究问题、方法或研究对象：

| 内容轴 | 示例性质 |
|---|---|
| 研究问题 | 成本、可靠性、组合推理、协作失真等不同问题 |
| 方法 | 检索策略、消融、图结构干预、记忆压缩等不同操作 |
| 对象与场景 | 不同任务或数据生成机制 |
| 验证计划 | 不同控制变量、比较基线或评价目标 |
| 假设/适用条件 | 不同前提、失败模式或限制 |

这些是fabricated fixture中的研究设定，不能捏造真实作者、DOI或已完成实验证据。标题、摘要和manifest都应明确synthetic=true；不把合成“结果”描述为现实发现。

为每个基础家族建立内容卡片：problem、method、setting、evaluation_plan、limitations。正文必须表达这些差异，而不是只在评估元数据里写“不同方法”。保存家族差异审计表。

### 3.2 重复与负例

扩充集同时加入：

- 每个主题至少1个家族的近重复变体，改变标点/明确的scenario或variant编号/轻微措辞；仍属同一gold家族。
- 同领域但与指定query无关的文献；无关性是query-relative，不能给一篇论文标全局irrelevant。
- 同主题、不同方法的非重复文献，用于检测误合并。
- 少量跨主题共享相关文献。
- 超过cutoff的测试文献，仅用于截点排除探针。

语料数据不能只为“短缺率=0”服务，单独保留可用簇为0/1/2的不足集合。不得在生产检索中自动生成或补入假文献填槽位。

### 3.3 真值隔离

生产语料字段：paper_id、title、abstract、year、field、topic_ids、synthetic及现有合法字段。

评估侧独立文件：gold_family_id、query_relevance_grade、query_id、annotation_reason、variant_of、shared_topic_relation、split。

- gold_family_id、相关性真值不能输入生产召回、排序、去重或缓存特征。
- 生产topic_ids可按既有规则用于attention；不能把评估者针对query的相关性标签当特征。
- 对语料/状态做schema白名单验证；调换gold标签只能改变评价，不得改变检索输出。
- 不以被测去重算法的cluster_id生成gold家族；gold由内容设计先定义。
- calibration/test按整个家族划分，变体、共享家族不得跨集合泄漏。

默认校准集约1/3家族，余下测试；明确实际数量。先冻结家族和query划分，再校准参数；测试后改参数须另起版本并记录，不再称为未见测试。

## 4. 最小代码修改方案

| 职责 | 需要实现/修改 |
|---|---|
| evidence builder | 确定性创建扩充fixture与来源、家族、相关性审计 |
| eval dataset loader | 分离生产语料和gold标签，校验ID与split |
| retrieval diagnostics | 增加逐阶段簇供给、政策特征跨度与选择变化 |
| gold cluster metrics | 按独立家族真值计算覆盖与误合并/漏合并 |
| policy probes | 固定候选池和状态的可解释取舍案例 |
| topic pair runner | 全主题对、空记忆、固定阅读历史比较 |
| engine adapter tests | 证明生产调用实际用到新模式并正确传递证据 |
| delivery validator | 复算行数、hash、不变量，输出分项验收 |

优先复用现有stage_a_fixed。只有实际发现缺陷时才改ranker或dedup；不得为了让对照出现差值修改排序公式。

可采用如下路径，实际按仓库映射：

```text
data/stage_a_supplement/corpus_expanded.jsonl
data/stage_a_supplement/gold_families.jsonl
data/stage_a_supplement/qrels.jsonl
data/stage_a_supplement/split_manifest.json
experiments/stage_a_supplement.py
experiments/policy_choice_probes.py
metrics/evidence_clusters.py
configs/stage_a_supplement.json
execute_stage_a_supplement.py
tests/test_evidence_clusters.py
tests/test_policy_choice_chain.py
```

## 5. 实验E1：语料供给与检索行为矩阵

沿用108个状态：12主题×3领域×3种语义记忆。阅读历史固定为空，其他状态和核心阶段A一致。

新增因子：2套语料×2个ranker×3种policy。

    108 × 2 × 2 × 3 = 1296次核心检索

若实际主题或领域数不同，按配置计算预期数量，不伪造12/3。

按以下方式解释比较：

1. 同语料、同状态、同policy比较两个ranker：实现版本对比。
2. 同ranker、同状态、同policy比较语料：证据环境整体变化。

语料扩充会改变候选内容、词法统计和主题attention；因此第二项不能全部归因于“独立证据数”。每套语料独立冻结一次合法attention，各policy共用；保存每主题attention及跨度。不强求不同语料attention相同，不为制造差异填假频率。

特别检查：若扩充语料使所有主题频率相同，应报告recognition区分度不足，不能悄悄增加热门主题样本直到得到预期policy效应。

对于仅增加重复变体的附加探针：主分析允许体现生产语料统计的真实变化；若做“仅隔离去重”的统计冻结实验，需显式标记controlled_statistics并固定基础家族统计，不把它当生产默认流程。

## 6. 实验E2：政策选择链路

### 6.1 诊断特征，而不只看集合

每个case输出：

```text
candidate_pool_hash
qualified_pool_hash
evidence_relevance_span
exploration_span
attention_span
policy_term_span
final_score_span
predicted_unique_cluster_count
gold_available_cluster_count
selected_order
selected_cluster_ids_eval_only
selection_difference_reason
```

至少区分：no_choice_capacity、features_constant、policy_score_changed_rank_same、rank_changed_set_same、selected_set_changed、path_disabled。

“无选择空间”应以合格且可区分候选簇数与top_k比较判定；不能只看原始文献数量。

### 6.2 两级验证，禁止fixture冒充生产证据

第一级：评分接口单元fixture。

允许输入明确标记的synthetic特征表，验证当前公式中的w_n/w_r是否正确进入policy项及final_score。至少6个等基础相关性的不同候选，包含高探索低attention、低探索高attention、中间取舍。数值预先冻结，通过手算结果验证。

此级别可直接设置特征，但只能称为评分接口测试，不能称为真实语料检索实验。

第二级：生产端到端检索fixture。

使用真实生产特征提取器计算词法相关性、相对阅读历史的探索与冻结主题attention；不能直接把第一级分数塞进生产结果。构建有足够候选、可解释取舍的专用fixture，与E1主语料分开保存。

若需要非均匀attention，应在该fixture的历史背景语料中预先定义计数结构，保存统计规则；不能直接按policy赋attention。候选合成内容与历史背景都不能根据运行结果动态改写。

固定query、Agent领域、语义记忆和候选池，仅改变policy。关闭retrieval政策路径时，三组使用同一中性上下文。

增加3种阅读历史：空、偏熟悉候选家族集合A、偏熟悉集合B。阅读历史是过去可见文献，不从当前运行未来结果生成。一次配对中只改变一个因子；历史变化实验不得同时更换语义记忆。

默认至少3个预先定义的取舍场景×3阅读历史×3policy×2ranker=54次端到端调用。单元特征表调用另计，不混入54或1296。

### 6.3 验收

- 同query/固定状态下三个policy基础池hash一致。
- 单元fixture的各分量与手算一致。
- 至少一个预先冻结的充分供给端到端场景，policy变化产生可解释的排序或所选集合变化，且由分数分解支持。
- 政策路径关闭后输出一致。
- 如果当前机制无法在合理fixture上产生变化，提交具体原因与failed/blocked状态，不继续调参到“出现变化”为止。
- 实际主矩阵无需保证每个case出现policy差异；balanced也无需在所有指标上位于另两组中间。

## 7. 实验E3：评估指标与合理重合

### 7.1 基于gold家族的新指标

针对query q，设G_q为截点内gold相关家族集合（相关等级≥1）；S_q为所选文献映射到的gold家族集合。

    gold_cluster_recall_at_k = |S_q ∩ G_q| / |G_q|
    gold_cluster_capacity_coverage_at_k = |S_q ∩ G_q| / min(k, |G_q|)
    gold_cluster_precision = |S_q ∩ G_q| / |S_q|
    selected_redundancy = 1 - |S_q| / selected_count

分母为0返回null并记录原因；无相关证据且返回空集的正确性由独立empty_result_correct判定。覆盖率不用于掩盖无相关query。

保留原文献条目coverage@3和标签匹配率，明确名称和口径，不回写旧指标。另报strict主题标签匹配和允许跨主题相关的gold相关性，防止memory/retrieval共享证据被机械当错。

gold不足3簇时容量覆盖率可以达到1，但必须同时报告available_cluster_count与shortfall，不能称为已满足3篇阅读预算。

### 7.2 去重质量

在冻结测试候选范围内比较gold同家族与预测同簇，输出pairwise precision/recall、误合并对、漏合并对及分母。算法自己的cluster覆盖仅作内部诊断，不能替代gold指标。

链式聚类、greedy聚类或输入顺序影响必须记录；输入顺序打乱后预测簇的成员集合应按设计保持一致，簇标签编号变化不当作失败。对不同研究被误合并的样例优先修复，不通过无限抬高阈值消除误合并而不报告漏合并。

### 7.3 全主题对

固定空语义记忆、空阅读历史，在每套语料、每个ranker、每个领域、每个policy中比较所有不重复主题对。

12主题有66对，故默认比较行数：

    66 × 2语料 × 2ranker × 3领域 × 3policy = 2376行

允许复用E1空记忆结果，不重复调用检索。明确区分retrieval_calls与comparison_rows。

根据预先标注把主题对分成共享证据、相近但无共享家族、远距离；报告文献集合Jaccard、gold家族Jaccard、排序变化。相同集合顺序不同另列，不能算集合改变。

共享证据对允许Jaccard>0，不能把“全部主题对Jaccard=0”作为验收标准。

## 8. 证据不足原因必须可定位

每次记录下列计数：

```text
corpus_eligible_doc_count
gold_relevant_cluster_count_eval_only
base_candidate_count
qualified_doc_count
predicted_qualified_cluster_count
selected_count
```

区分真实供给不足、基础召回遗漏、门槛排除、去重合并、其他上限。原因可以多项并存，记录触发阶段和计数，不把所有shortfall统一写成near_duplicate_limit。

去重前候选池截断若被重复文献占满，记录duplicate_crowding：全语料有足够相关gold簇但base pool遗漏。先诊断；如修改为更大预召回再去重截断，必须显式配置pre_pool_budget，两个policy组共用，并单独报告计算预算变化，不能暗中扩大召回预算。

## 9. 实验E4：小规模生产接口回归

保持原20 Agent结构、原六阶段与社会规则。默认只运行一个seed、一个balanced/open配置、12 tick的mock smoke，新目录保存；这是接口回归，不计算政策效果。

另用生产检索入口和Idea输入构造器做至少4个定向案例：0、1、2、≥3个合格独立证据簇。

- 证明返回的证据ID/正文实际进入mock backend输入，不只存在于检索日志。
- 无证据时执行已有wait/low_evidence规则；若仓库没有清晰规则，优先wait并记录，不引用不存在的论文。
- 少于3篇时正常传递真实数量，不补假文献。
- 引用校验不得把未检索且不可见的文献当有效证据。
- 原有实际投入计量规则保持不变；验证少文献或失败路径不会重复入账、零分母处理正确。
- 跑受影响的状态/账本测试，不重跑全部多seed科研社会实验。

## 10. 参数冻结、测试与继续执行规则

先冻结语料、gold、splits、query和policy fixture，再运行reference与supplement。fixture seed默认5101，执行顺序seed=5102；种子仅用于确定性样本与顺序，不宣称统计重复。

必须测试：gold隔离、家族split无泄漏、共享文献不复制、缓存包含新语料/状态、policy关闭、手算排序、gold簇指标手算、0/1/2簇边界、误合并/漏合并、截点过滤、生产输入引用一致。

核心阶段A的冻结hash、冷/热缓存和执行顺序验收继续有效。保留原测试，记录原有失败与本次引入失败。关键运行时校验不能仅依赖assert。

如果扩充集gold≥3簇但检索不足3，应导出失败案例：相关簇在哪一步丢失。可以报告真实未解决问题，但不能把该项自动判passed。新语料不能因测试失败被选择性删掉。

## 11. 配置与命令交付

创建完整配置，至少包含corpora、rankers、topics、fields、memory_conditions、policy_paths、read_history_mode、gold_paths、splits、policy_scenarios、top_k=3、candidate_pool_size=20、seeds、output_root、allow_network=false、allow_llm_calls=false。

字段示例只用于说明职责，最终必须生成可运行配置。实现以下命令或兼容封装，并提供--help：

```bash
python3 execute_stage_a_supplement.py check
python3 execute_stage_a_supplement.py prepare --config configs/stage_a_supplement.json
python3 execute_stage_a_supplement.py test
python3 execute_stage_a_supplement.py run --config configs/stage_a_supplement.json
python3 execute_stage_a_supplement.py analyze --run-dir <运行目录>
python3 execute_stage_a_supplement.py validate --run-dir <运行目录>
python3 execute_stage_a_supplement.py package --run-dir <运行目录>
```

一键执行：

```bash
python3 execute_stage_a_supplement.py all --config configs/stage_a_supplement.json
```

all依次检查、准备并冻结数据、测试、E1/E2/E3/E4、复算分析、验收和归档。支持--dry-run以及按case恢复；输入或配置hash不一致时新建run，不混用结果。Windows使用所激活环境的python，Linux可用python3；文档记录实际解释器。

## 12. 输出与可复核归档

必须包括：

```text
SUPPLEMENT_PLAN.md
SUPPLEMENT_REPORT.md
config.json
manifest.json
status.json
usage.json
evidence_supply_audit.csv
family_content_audit.jsonl
frozen_states.jsonl
core_results.jsonl
candidate_scores.jsonl
policy_unit_results.jsonl
policy_e2e_results.jsonl
topic_pair_results.csv
gold_cluster_metrics.csv
dedup_error_pairs.csv
shortfall_attribution.csv
engine_integration_results.jsonl
failure_cases.jsonl
tests_report.json
DELIVERY_VALIDATION_STAGE_A_SUPPLEMENT.json
reproduction/（相关源码、fixture、gold、配置和必要依赖说明）
```

本次归档应包含实际使用的fixture与gold及相关源码，解决上一轮仅有报告/hash、无法独立复跑的问题。打包前排除密钥、缓存凭据、环境目录、旧大型归档；不重复嵌套zip。附文件清单和checksum。

报告必须同时展示：

- 原语料reference、原语料supplement、扩充语料reference、扩充语料supplement四个条件。
- 每个主题的gold供给、返回数、gold簇覆盖、shortfall原因；不能仅给总体均值。
- policy分数变化、排序变化、集合变化的case数及分母。
- 共享证据主题对与远距离主题对的差异。
- 哪些改进来自新增证据、哪些来自算法修改、哪些仅是指标口径变化。
- 实际完成的检索调用数、配对比较行数、测试数、smoke规模和0 API成本。

## 13. 验收及结论范围

分项状态至少包括 baseline、corpus_content、gold_isolation、splits、core_matrix、policy_unit、policy_e2e、cluster_metrics、topic_pairs、shortfall、engine_integration、offline_execution、reproducibility。

每项保存status、checked_count、failed_count、evidence_path、reason。数据行为测试不能仅以文件存在或非空通过。

完成标准：

1. 每主题至少6个独立家族的设计被内容卡和gold支持；共享ID与变体正确处理。若实际不足，明确未达标。
2. 默认1296核心调用与2376全主题对比较完整，行数从实际配置独立复算。
3. 独立gold指标和去重错误对可由原始数据复算，不读取被测聚类作为真值。
4. 充分供给的policy fixture验证从特征→效用→排序→选择的链路；缺少响应时不能用“所有policy本来就该相同”跳过。
5. 0/1/2簇情况下如实shortfall；足够gold供给但shortfall的案例有归因，不靠填充掩盖。
6. 生产接口证据传递和小规模mock回归通过；原社会机制未被无意修改。
7. 零网络/LLM调用，旧数据不变，归档足以复核。

只有必需项通过，overall才为completed；否则completed_with_limitations/failed并列出未完成项。通过仍只表示合成证据条件下的工程与机制可测试性，不代表独立科学质量验证完成。不得把648/1296等确定性组合当作随机世界计算政策显著性。

## 14. 用户给Codex的启动指令

请读取 SCIMIRROR_STAGE_A_SUPPLEMENT_CODEX_TASK.md，在当前已完成阶段A的SciMirror仓库上执行补充实验。保留已有修复、旧结果和用户修改；补充具有实质内容差异的合成证据、隔离gold家族与相关性标签、验证policy选择链路、完善证据簇指标和全主题对分析，并做小规模生产接口mock回归。按文档完成代码、配置、测试、离线实验、分析和可复核归档。默认不联网、不调用LLM或收费API，不重构科研社会机制，不通过强制改变文献或调参到显著来验收。请持续执行到交付完成；发现未解决问题如实标记并给出证据，不只回复计划。
