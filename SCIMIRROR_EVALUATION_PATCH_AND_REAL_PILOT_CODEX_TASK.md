# SciMirror：评价工具修补与真实文献小试验执行文档

版本：1.0 ｜ 日期：2026-09-17

仓库：https://github.com/PercyZhong/SciSocialSimulation

本次在线核对提交：`e0e95515c6cbce7b3e370c3316cdd56b3f741386`。

本文件是交给 VS Code Codex 的执行规格，不是已经完成的代码修改或实验报告。

## 0. 给 Codex 的任务

直接完成本文件要求的代码修补、离线验证、旧结果重新分析、人工审阅资料、真实文献小试验工具和交付归档。不要只提供计划。

本轮目标是修复评价与状态管理，不再进行大规模合成调参。保持 stage_a_fixed、stage_a_semantic_guarded、stage_a_repaired_v1 的检索行为和既有社会机制不变，不重选 Stage A 的 c05，不修改原 gold 或历史归档。

先阅读 AGENTS.md，检查 HEAD 和未提交改动。如果已有新实现，复用正确部分，记录与本文基准的差异。不得强制 reset、覆盖用户修改、自动推送或合并。

Linux/Python≥3.11 为权威环境，沿用现有 unittest 和依赖。默认离线，无 LLM、付费 API 或自动爬取；真实数据使用用户提供或仓库已有、具有来源记录的本地文件。不得伪造论文、摘要、人工查询、评分、审阅身份或成功状态。

缺少真实数据或人工评分时，先完成全部可执行的工具开发、测试、模板、旧结果分析和归档，再给出明确缺件清单。工具交付完成与真实实验完成分别判断。

## 1. 已核实问题和历史参照

| 已核实位置 | 问题 | 本轮要求 |
|---|---|---|
| retrieval_evaluation.py::regression_checks | key 不含 memory_condition，三种条件互相覆盖 | 完整分层匹配，另算宏平均 |
| retrieval_evaluation.py::evaluate_selection | 只接收 relevant，不能区分负标签和未标注 | 显式传递 judged、unknown、unsure |
| stage_b_retrieval.py::analyze | 有少量双评即 completed，只输出一致性 | 完成率驱动状态，补检索指标 |
| stage_a_repair.py::validate | stage_b_tooling_status 固定写 passed | 读取实际验证证据 |
| stage_b_retrieval.py::pool | 两个门控检索器的参数硬编码，包括0.8 | 冻结明确的 Stage B 配置与语义版本 |
| Stage B family_map_template | 有模板，无完整导入和版本管理流程 | 实现导入、冲突检查、family/split 审计 |
| import_annotations | 更新同一评分直接覆盖 | 追加修订记录，保留旧版本与变更理由 |

上一轮合成诊断参照：旧扩充语料上 fixed 覆盖率90.74%，Closure47.22%，Repair66.67%；Repair有4个主题共108/324次空返回。旧回归记录3项失败；science_evaluation因基线本身很低不构成该门槛下的新增退化，但仍是未解决项。

真实校准6组、72次生产检索，选中c05（threshold0.9/window18/beta_memory0.05）。校准的memory为空，不能宣称0.05已被验证优于0.10。

这些只是历史参照，重新分析从原始case计算，不把参照值硬编码成实测输出。

## 2. 代码范围

优先修改现有模块，避免重写平台：

- `scimirror/retrieval_evaluation.py`：分层回归、未标注语义及可复用指标。
- `scimirror/stage_a_repair.py`：接入新评估器，独立的旧结果重分析。
- `scimirror/retrieval_gold_audit.py`：人工审阅导出、裁决导入和版本管理。
- `scimirror/stage_b_retrieval.py`：真实数据、pool、标注、裁决与状态流程。
- `execute_stage_b_retrieval.py`：增加必要子命令。
- `configs/stage_b_retrieval_small.json`：新建30–50篇/6–9查询配置。
- `tests/test_retrieval_evaluation.py`、现有Stage B测试：补必要反例。
- 新建 `execute_evaluation_patch.py` 作为本轮一键交付入口。

允许将Stage B指标与状态机拆成小模块。旧schema保持可读，新增结果显式使用新schema/evaluator_version，不让同名指标悄悄改变含义。

## 3. 修复Stage A回归：完整维度、未知值和复算

### 3.1 维度

基础汇总键为：

```text
(dataset_id, ranker, topic_id, memory_condition)
```

如果以后增加其他分层，必须显式扩展schema。当前每组应包含3 fields×3 policies=9个case，校验组合而不只检查行数。

比较fixed与repaired时，按dataset/topic/memory匹配。重复键直接报错，缺对照标记incomplete，不能continue后当作通过。

同时从case计算跨memory的主题均值和跨主题宏平均；不能取最后一行代表主题，也不能在不等样本量情况下直接平均均值。

保留原冻结下降阈值0.05。每条失败记录metric、memory_condition、baseline、treatment、difference、分子分母。区分failure_record_count与failed_topic_count，不能把同主题多个memory失败误称多个新主题。

另输出 unresolved_topics.csv，列出长期低质量/空返回主题，避免“未新增退化”被解释为已解决。

### 3.2 标注状态

每个query-document或query-family关系采用：

```text
judged_positive: grade 1 or 2
judged_negative: grade 0
unjudged: 没有标签
unsure: 评分者明确无法判断
pending_adjudication: 两人不一致，尚未裁决
```

缺失标签和unsure不得转成0。保留build_gold返回的judged映射贯穿所有调用方；严格校验非法grade、未知ID和冲突标签。

旧合成qrels是family级：明确允许按family标签映射到成员文献。真实人工评分是document级：不能因为同family的一篇有评分就复制给所有版本。先选冻结canonical文献，或分别评分后再聚合family指标。

### 3.3 复算与缓存

提供 `reanalyze --input-run PATH --output NEW_PATH`。从1944个旧selected IDs、原始qrels和配置复算，不运行检索、不重新校准、不覆盖旧结果。

输出修补前后指标与失败记录的diff，说明哪些变化来自评估器修复、哪些是原问题的展开。selected IDs哈希必须保持不变。

如本地缺少旧run，记录missing_previous_run，仍完成其他代码与测试；不擅自下载并执行未知文件或为了补结果重跑合成调参。

## 4. 人工审阅四个空返回主题

目标主题：learning_exploration、learning_reward、science_collaboration、science_evaluation。

从旧正标签文献生成human_review包。内容含topic定义、原文标题摘要、旧grade、family、来源版本、待填写裁决、理由与原文证据。保留原始提案，但人工首轮表不预填自动结论或检索得分。

裁决表字段：

```text
review_id, topic_id, paper_id, gold_family_id
decision (retain/revise/insufficient_information)
proposed_grade, rationale, evidence_excerpt
reviewer_id, reviewed_at, base_qrels_hash, review_version
```

import-gold-decisions 校验非空身份/时间/理由、合法ID、证据摘录可定位、旧版本匹配。存在family成员裁决冲突时，不能直接生成唯一family标签；进入conflict队列。

自动报告只能叫triage/proposal，不得把关键词齐全等同于人类确认。工具不能证明实际身份或独立性，只保存可追溯声明和导入记录，不通过字符串“非Codex”就声称人工真实性已验证。

生成新qrels为新版本，不替换原始文件。旧qrels结果和人工裁决后结果双列；未裁决项保留，不自动删除以改善分母。人工输入缺失时状态awaiting_human_review，不阻止工具交付。

## 5. Stage B小试验：范围和冻结规则

目标是先验证完整流程，不以少量查询比较得出算法普适优势。

- 3主题：agent_memory、science_retrieval、science_evaluation。
- 30–50篇去重后、符合年份条件、有标题摘要和来源的真实文献。
- 6–9条人工自由文本查询，每主题2–3条；author_type必须如实记录。
- 两名固定、独立评分者，第三位/指定人员可处理分歧。
- policy、semantic memory、field context重排均关闭。
- 暂不接LLM生成Idea，不改变科研社会模拟规模。

为降低盲评遗漏偏差，本轮默认对整个小语料逐query标注：30–50篇×6–9查询=180–450对，两人合计360–900个评分。若人工工作量需要改成候选池标注，可在freeze前显式切换 `annotation_scope=pooled_top10`；不得事后切换以改善结果。

全文献标注使小试验可计算该有限语料的召回；候选池标注只能计算pool内召回并报告未标注覆盖。

### 5.1 时间边界和来源

沿用 `year < cutoff_year`，默认cutoff_year=2025，只包含2024及以前文献。如用户要包含2025/2026文献，创建明确的新配置和run版本，不能悄悄改变历史边界。

必需字段：paper_id/title/abstract/year/source_url/synthetic=false。拒绝null、纯空白、非整数年份；不得将None转成字符串作为有效摘要。输出导入前、去重后、年份过滤后的数量和排除原因。data_ready以过滤后的有效文献数判断，至少3篇只说明加载器可用，不代表达到本轮30–50篇目标。

保留doi/arxiv_id/version/source_provider/retrieved_at。来源信息缺少时标记待核验，不能自动补写真实作者、摘要或出版信息。离线URL检查不等于在线验证。

不自动抓取全文；用户可从已下载文献或合法元数据导出中填充本地文件。

### 5.2 family与版本

实现import-family-map，必须实际生效，不仅生成模板。规范DOI/arXiv版本，处理跨标识的重复和正文重复；模糊相似仅产生待审候选，不自动大规模合并。

每篇文献恰好映射到一个family，canonical代表选择确定且可追溯。不因为同一主题就归为同一family。对同family多个版本保留别名映射，检索比较默认使用冻结canonical代表。

小pilot所有数据可标记pilot，不必人为拆出过小“测试集”，也不宣称未见泛化。后续扩大规模如使用calibration/test，文献family不跨split，查询划分独立记录并实际过滤执行。未知split必须拒绝，不接受任意字符串。

### 5.3 查询与检索配置

查询不从positive_phrases批量生成。工具提供空模板和仅供说明的示例，示例不得混入正式数据。要求同主题不同query_text确实影响检索。

三个比较方法保留现有Stage B算法行为，并准确命名/说明：

1. BM25 lexical：自由文本词法基线。
2. Closure gate + BM25 rerank：topic gate后取最多20候选，再用query_text重排。
3. Repaired gate + BM25 rerank：修复后的topic gate，其他步骤同上。

当前Stage B并非直接重放Stage A的c05，门控方法还有 `evidence + 0.2*normalized_BM25` 步骤。将所有常数迁入配置：BM25 k1/b、gate threshold、window、pool size、query weight、去重规则、tie break。默认显式保留现有0.8并标记 `parameter_origin=stage_b_predeclared_not_stage_a_selected`；0.9只作为历史c05说明，不在本轮偷偷增加参数搜索。

保留三种方法真实差异，不强行保证候选池一致。记录free-text介入前后的rank，以揭示topic gate导致的漏召回。

BM25全零分时显式记录zero_signal，不把按ID排序解释为相关性证据。为保持既有基线可比性，可保留其确定性top-k，但必须在报告标注该行为；不要暗改为另一种算法。

pool记录每个query×ranker的独立run行，包括空返回。现有只保存非空mapping的方式会使空检索器从分析中消失，必须修正。

freeze写入语料、queries、family、配置、profiles、源码依赖、评估器版本和reviewer registry哈希。开始标注后这些输入变化必须新建pool_version/run，旧评分不得无校验地套用。

## 6. 固定双评、裁决与状态管理

### 6.1 注册与盲化

reviewers.json预先固定A/B的匿名ID和role，真实姓名非必需。裁决者单独登记。禁止按每条记录的reviewer字母顺序临时挑两人。

导出两个独立评分包。每人只看到query、原文、来源、随机顺序和blind_id，不看到ranker/分数/原rank/另一人的评分。私有映射不打入公共标注包。随机种子与blind_id绑定pool_version以便恢复。

### 6.2 评分语义

0=与查询无关；1=部分支持查询；2=直接支持查询；unsure=信息不足或无法判断。均要求理由与rated_at。评分对象是“文献与查询的相关性”，不是论文整体质量或生成Idea质量。

同一reviewer重复导入完全一致的记录为幂等；改分必须使用revision并写明变更理由，追加历史，禁止静默覆盖。

未完成评分允许增量导入。unsure算已提交但不算已解决数值标签。输入冲突、未知ID、非法分数、pool_version不匹配必须明确报错。

### 6.3 完成率

设期望query-document对数为N：

```text
expected_ratings = 2*N
submission_coverage = 已提交(A或B，包括unsure) / (2*N)
double_submission_coverage = A和B均提交的对数 / N
double_numeric_coverage = A和B均给0/1/2的对数 / N
resolved_coverage = 已得到最终数值标签的对数 / N
```

两个数值评分相同可生成agreement标签；不一致或unsure进入裁决队列。默认不采用平均分、最大值、第一人评分自动替代裁决。

裁决保存原两评分、最终grade、裁决者、依据、时间和版本。无法裁决仍为unresolved，不自动标0。

一致率和线性加权Cohen kappa只使用裁决前固定A/B的双数值评分；部分标注时可探索性计算并带coverage。常数评分导致期望分歧为0时κ=null，写明原因。若导入第三人的评分，不能悄悄替换A/B。

### 6.4 状态

分别维护tooling、data、queries、family、freeze、pool、annotation、adjudication、analysis。推荐状态：

```text
awaiting_real_corpus / awaiting_human_queries / awaiting_reviewers
ready_to_freeze / frozen / pooled
awaiting_human_labels / partially_labeled / awaiting_adjudication
ready_for_analysis / analysis_completed
```

只有固定范围全部标注并解决、输入有效且指标成功输出，才能analysis_completed。部分结果使用partial_analysis，不把有1条双评当作完成整个实验。

完整标注范围为零不是100%完成，而是invalid_empty_scope。工具ready从实际测试验收读取，不能写常量passed。科研因果主张状态始终false，本轮是检索小试验。

## 7. 正式指标口径

逐query、逐ranker输出k=3/5/10，另汇总topic宏平均和query宏平均。所有表同时输出分母、returned_count、shortfall和未标注数。

### 7.1 未标注处理

设L为实际返回前k文献（不足不填造文献），j为其中已解决标签数，r为grade≥1数，u为未解决数。

```text
judged_coverage_returned = j/|L|（|L|=0时null，并empty=true）
precision_judged = r/j（j=0时null，仅描述已标注部分）
precision_at_k_lower = r/k
precision_at_k_upper = (r+u)/k
```

缺少的返回槽位是真实shortfall，不是不确定标签，不加入u。实际返回全已标注时，正式P@k=r/k，另报precision_returned=r/|L|。有unjudged时正式P@k=null，展示上下界而非静默填0。

空返回P@k=0、returned_precision=null；如果语料无任何相关文献，recall=null并报告no_relevant_supply。指标k必须参数化，不能固定写3。

### 7.2 nDCG

```text
gain(grade)=2**grade-1
DCG@k = sum(gain_i / log2(i+1)), i从1开始
nDCG@k = DCG@k / IDCG@k
```

IDCG来自该query的共同评价范围中已完成裁决的标签，而不是各检索器自己的返回列表。所有比较方法使用同一个IDCG。

full_corpus范围未完整标注时，不报告正式nDCG；可输出partial诊断但不能用于正式排名。pool范围完整时叫pooled_nDCG@k，说明范围。IDCG=0时nDCG=null并报告原因。

### 7.3 Recall和重复

full_corpus全部已判定时可报有限语料Recall@k；pool模式仅报pooled_recall@k。分母为范围内相关文献/family数，不声称覆盖未知外部文献。

family指标由冻结映射计算，重复返回不能重复累计family召回。document级和family级分别命名。不能从未标注family推定无关。

### 7.4 小样本解释

6–9条query只做逐query差异和描述性均值，不宣称显著性或稳健优势，不把文献数、评分数当独立query样本。可以列paired differences；本轮不需要bootstrap排行榜。

## 8. 必需手算和反例测试

新增测试只围绕具体风险，不为凑测试数量。

1. empty/related/unrelated三行同主题，只有related退化，也必须被识别；缺对照/重复键报错。
2. rank列表grade为[2,unjudged,0]，k=3：P正式=null，下界1/3，上界2/3，judged precision=1/2。
3. 返回[2,0]，k=3：P@3=1/3、precision_returned=1/2、shortfall=1。
4. 返回[2,0,1]，共同gold grades=[2,1,0]：DCG=3.5，IDCG=3+1/log2(3)，nDCG约0.96394。
5. 空返回与全零gold分别处理，避免空集合“100%覆盖”。
6. N=10只有1对双评，状态必须partial；19/20评分也不能完成。
7. 固定A/B一致性不被C评分改变；裁决不改写原始κ；常数评分κ=null。
8. family跨split、pool版本变化、非法身份/grade、修改旧评分无revision均被拒绝。
9. null摘要、年份过滤后不足30、重复DOI/arXiv版本，计数及排除原因正确。
10. query×ranker空返回仍出现在指标表；自由文本查询确实影响排序。
11. 在test fixtures目录用明确synthetic测试数据走完整流程，所有输出标记test_only，不能写入真实pilot目录或宣称是真实人工评分。
12. 更新gold只使评估缓存失效，不能改变生产selected IDs。

保留AGENTS.md要求的doctor/test和必要完整mock回归。由于本轮修改指标与数据处理，运行18分支mock并验证20Agent/tick30、共同分叉和事件回放。该回归不等于再做合成调参。

## 9. 命令接口和自动执行顺序

扩展现有execute_stage_b_retrieval.py，保留旧兼容入口。新增命令可调整名称，但必须更新--help、README并实际测试。

```bash
# 本轮Codex实现并执行的总入口
python3 execute_evaluation_patch.py all --output outputs_evaluation_patch/patch_001

# 旧结果只重分析，不重跑检索
python3 execute_evaluation_patch.py reanalyze --input-run outputs_stage_a_repair/EXISTING_RUN --output outputs_evaluation_patch/patch_001/reanalysis

# 真实小试验，路径由用户本地文件替换
python3 execute_stage_b_retrieval.py init --config configs/stage_b_retrieval_small.json --output outputs_stage_b/pilot_small_001
python3 execute_stage_b_retrieval.py import-corpus --run-dir outputs_stage_b/pilot_small_001 --input real_papers.jsonl
python3 execute_stage_b_retrieval.py import-family-map --run-dir outputs_stage_b/pilot_small_001 --input family_map.csv
python3 execute_stage_b_retrieval.py import-queries --run-dir outputs_stage_b/pilot_small_001 --input queries.csv
python3 execute_stage_b_retrieval.py import-reviewers --run-dir outputs_stage_b/pilot_small_001 --input reviewers.json
python3 execute_stage_b_retrieval.py freeze --run-dir outputs_stage_b/pilot_small_001
python3 execute_stage_b_retrieval.py pool --run-dir outputs_stage_b/pilot_small_001
python3 execute_stage_b_retrieval.py export-annotation --run-dir outputs_stage_b/pilot_small_001
python3 execute_stage_b_retrieval.py import-annotations --run-dir outputs_stage_b/pilot_small_001 --input reviewer_a.csv
python3 execute_stage_b_retrieval.py import-annotations --run-dir outputs_stage_b/pilot_small_001 --input reviewer_b.csv
python3 execute_stage_b_retrieval.py export-adjudication --run-dir outputs_stage_b/pilot_small_001
python3 execute_stage_b_retrieval.py import-adjudication --run-dir outputs_stage_b/pilot_small_001 --input decisions.csv
python3 execute_stage_b_retrieval.py analyze --run-dir outputs_stage_b/pilot_small_001
```

init将config复制到run；后续命令默认使用run内配置，不退回CLI默认大规模配置。不同配置覆盖须新run或显式版本迁移。重复init不得清空已有评分；pool在标注开始后不得覆盖旧版本。

all先执行所有代码、测试、旧结果复算、审阅资料与模板交付。若真实输入已提供则自动推进至待人工标注；若无输入则输出NEXT_INPUTS.md并结束，不冒充真实pilot已完成，也不要为继续开发工具反复询问用户。

可选本地输入参数明确暴露在CLI：--previous-run、--real-corpus、--queries、--family-map、--reviewers。自动找历史run时按config/schema/manifest识别；多份候选不静默选择错误数据，列出候选并将重分析标记needs_run_selection，其他工作继续。

## 10. 交付、复现与退出状态

必须交付：

```text
CODE_CHANGE_REPORT.md
FINAL_REPORT_ZH.md
DELIVERY_VALIDATION.json
tests_report.json
runtime_manifest.json
reanalysis/（若旧输入可用）
human_review/（表格、指南、导入命令）
pilot_templates/（真实论文、query、family、reviewers模板）
ANNOTATION_GUIDE_ZH.md
NEXT_INPUTS.md
reproduction/source/
reproduction/REPRODUCTION_RESULT.json
CHECKSUMS.sha256
```

保留目录结构、依赖、必要fixtures和配置，在独立临时解压目录实际运行评估器测试及test_only端到端；核对模块路径与指标语义。不要将真实人工待输入当作复现失败，也不把测试假评分打包成真实标签。

归档排除环境、密钥、缓存、重复历史zip；校验清单不包含自身。网络实验调用默认0，依赖安装与实验调用分开计数。

DELIVERY_VALIDATION分开记录：

```text
code_patch_status
evaluation_unit_status
stage_a_reanalysis_status
legacy_quality_status
human_gold_review_status
stage_b_tooling_status
real_data_status
annotation_status
adjudication_status
analysis_status
reproduction_status
```

工具交付成功可以退出0并注明真实实验pending；真实analyze命令在标注未完成时返回partial状态及非成功完成码（建议2），不能把整个任务标记analysis_completed。异常为1。任何旧质量失败必须保留，与代码是否修好分开。

最终中文报告说明：本轮改了什么、哪些指标因为评估器修复变化、旧检索结果是否保持、哪些人工输入尚缺、下一条可运行命令是什么。

## 11. 停止条件与后续扩展

当评价修补、必要测试、旧结果重分析（如可用）、人工审阅材料和真实小试验工具交付后，结束本轮。不要为了拿到全passed再修改检索规则、重新校准或生成正例。

真实pilot完成后，由人工确认流程无泄漏、来源可追溯、完成率正确、指标能解释，再创建新run扩展到100–200篇/20–30查询。小pilot接触过的数据标记为development_seen，不重新包装成未见测试。

## 12. 用户给Codex的启动语

请完整读取本文件与AGENTS.md，直接实施评价工具修补、旧结果重分析和真实文献小试验流程。不要重新进行大规模合成调参。缺少真实文献或人工评分时，完成全部代码、测试、模板及归档并标记pending；不得编造论文、人工查询或评分。保留工作区修改和历史结果。
