# SciMirror Stage B：候选召回与排序退化诊断执行文档

版本：1.0  
适用仓库：PercyZhong/SciSocialSimulation  
执行方式：将本文放入项目根目录，交给 VS Code 中的 Codex 执行。

## 0. 给 Codex 的执行指令

请完整执行本文，完成代码改进、必要测试、固定离线实验、中文报告和结果归档，不仅提供计划。先阅读 AGENTS.md、上一轮执行文档和当前实现，检查 git status，保留用户未提交修改。复用已有 Stage B 软重排、语义哈希、阶段追踪和独立复现功能，不重新设计平台。

本轮独立人工核查已由用户延期，不作为执行前置条件。保持原评分、裁决及来源不变；不调用网络检索、LLM或收费API，不重复调参，不改变生产 Agent 检索路径。

完成本轮固定实验即停止。用户将把报告及 ZIP 发回进行下一轮判断，不自行继续扩大任务。

## 1. 当前依据与目标

上一轮用户转述的结果如下，执行时必须从归档核对，不能直接当作本轮验收证据：

- 9查询×6臂，54条运行记录、1,944条逐文献trace。
- 324/324标注对迁移成功；历史27个排序、81个指标差异为0。
- lambda=0与BM25完全一致；历史归档checksum不变；隔离目录复现成功。
- 仅软重排相对BM25的nDCG@5差值为−0.005948。
- 对齐画像带来+0.016651增量；最终臂相对BM25为nDCG@5 +0.010704、P@3 +0.037037。显示值经过舍入，不要求舍入后的差值严格相加。
- agent_memory和science_retrieval的topic nDCG@5下降，candidate_for_future_validation=false。
- 旧排序器在3个science_evaluation查询上因topic_gate的score=0、threshold=0.8丢失相关文献。
- 新对齐方案第三条science_evaluation查询仍有1篇相关文献在BM25 top-20阶段丢失。
- 专项测试16项、完整测试118项通过；已报告Windows mock验收。

本轮回答四个问题：遗漏文献在BM25中排第几？扩大候选能否使它进入最终结果？另外两个主题为何下降？候选扩展带来的变化是否夹杂归一化尺度变化？

## 2. 固定边界

1. 使用上一轮同一批36篇文献、9条查询、canonical family集合、324对评分及其历史。
2. 不修改lambda=0.10、画像、查询、BM25参数、原标签及裁决。
3. 不新增人工评审、不做参数搜索、不扩充语料。
4. 同一时间截止、最终top-10、并列处理、零信号规则；不引入额外去重或正分过滤。
5. evaluation_split固定为exploratory_reused_pilot；ready_for_scientific_claims=false。
6. 工程验收与算法改善分开；算法未改善也应完成并交付报告。
7. 历史目录只读，新目录写结果；不覆盖旧归档、不自动push或部署。

## 3. 输入核对与Linux验收

定位上一轮完整运行目录，核对54运行、1,944条追踪、324评分对、历史复现记录、lambda0一致性和checksum。记录实际源码版本、工作区差异、配置与语义哈希、Python版本及平台。

若已有本轮相同代码版本和配置的Linux有效验收，验证后复用并记录来源；否则根据AGENTS.md在Linux完成测试与完整mock验收。Windows验收不能替代Linux权威验收。

若只能使用Windows，完成代码与本地测试，生成Linux运行说明，标记linux_authoritative_validation=pending，不假报Linux通过。若输入归档缺失，仍完成可完成的代码和fixture测试，列出missing_inputs.json；不得以合成数据替代真实pilot并宣称完成。

存在多个不等价归档时输出候选清单，不根据结果优劣选输入。优先接受显式--source-run或--source-archive。

## 4. 代码组织与执行入口

先定位上一轮实际新增模块和CLI，以最小改动扩展。建议职责如下，文件名可适配现有代码，并在报告写明对应关系：

| 模块 | 修改职责 |
|---|---|
| 现有软重排模块 | 候选规模支持20和all；导出归一化分母；支持固定分母诊断 |
| 现有阶段追踪模块 | 完整BM25排名与候选遗漏定位 |
| 新增candidate_diagnostic分析模块 | DCG贡献分解、召回表、退化报告 |
| 现有语义哈希模块 | 纳入候选规模、归一化规则、诊断分母来源 |
| 现有CLI或独立诊断CLI | run、validate、reproduce及可恢复执行 |
| 测试模块 | 本文第9节的必要不变量 |

推荐独立入口execute_stage_b_candidate_diagnostic.py；若扩展现有CLI，同样提供等价命令。以下命令是本轮需实现的接口示例，不能假设已存在：

```bash
python3 execute_stage_b_candidate_diagnostic.py run --source-run /path/to/previous_run --output outputs_stage_b_candidate_diagnostic/run_01
python3 execute_stage_b_candidate_diagnostic.py validate --run-dir outputs_stage_b_candidate_diagnostic/run_01
python3 execute_stage_b_candidate_diagnostic.py reproduce --run-dir outputs_stage_b_candidate_diagnostic/run_01 --output outputs_stage_b_candidate_diagnostic/reproduced_01
```

参数保存到配置文件并在执行前冻结。结果缓存必须校验所有有效依赖，不因文件名相同就复用。不兼容的已有输出目录应拒绝覆盖；中断后按完成清单恢复，不重复调参。

## 5. 完整候选召回诊断

对9条查询保存所有canonical文献的BM25排序。当前应为9×36=324行，唯一键(query_id,paper_id)。检索阶段不含标签；评价阶段事后关联评分。

bm25_full_ranking.csv字段至少包括：

```text
query_id,topic_id,paper_id,family_id,bm25_score,bm25_rank,zero_score,
final_grade,reviewer_01_grade,reviewer_02_grade
```

candidate_recall.csv字段至少包括：

```text
query_id,topic_id,label_view,relevance_rule,candidate_k,
eligible_document_count,relevant_total,relevant_retrieved,
recall,unjudged_count,zero_score_candidate_count
```

固定报告K=10、20、all（目前36）。分别使用grade>=1和grade=2；原评审unsure/缺失为未知，不填0。标签不完整时不得将已知相关分母的结果冒充完整Recall，需明确partial状态。

对上一轮遗漏文献输出query_id、paper_id、BM25排名与分数、是否零分、K20/all是否入候选、最终top5/top10是否返回。

全语料候选Recall=1是集合覆盖的机械结果，不能作为排序质量提升结论。分母为0写null并说明原因。

## 6. 排序退化归因

固定比较上一轮BM25、原画像软重排、对齐画像软重排，生成ranking_changes.csv：

```text
query_id,topic_id,paper_id,bm25_rank,soft_legacy_rank,soft_aligned_rank,
bm25_raw,bm25_normalized,topic_score_raw,topic_score_normalized,
weighted_topic_bonus,final_score,final_grade
```

画像对应的分项分数不同，实际实现可用long format：每(query,ranker,paper)一行，避免用一个topic_score混指两种画像。保留可直接比较的rank变动表。候选外排名填null并写原因，不能虚构名次。

ranking_regression_analysis.csv逐查询报告：

- nDCG@5、P@3变化。
- 进入/退出top3、top5的文献。
- 高相关文献被低相关文献挤出，或相关文献在top5内部降位的情况。
- 各文献对DCG@5差值的贡献，及加和复核误差。

DCG贡献定义：令文献在某方法的贡献为(2^grade−1)/log2(rank+1)，不在top5贡献为0；新方法贡献减基线贡献，逐文献相加必须等于总DCG差。nDCG分解使用同一查询、同一标签视图下相同IDCG；IDCG=0时标明不可定义，不静默除零。

区分候选损失与排序损失。前者是未入候选；后者是已在候选却因加分而改变相对次序。为agent_memory和science_retrieval给出具体文献证据，不只报告宏均值；不要因此修改画像或权重。

## 7. 固定候选规模实验

主矩阵：9查询×5臂=45条运行记录。

| ranker_id | 候选 | lambda | 画像 |
|---|---|---:|---|
| bm25 | 原基线 | 不适用 | 不使用 |
| soft_legacy_k20 | BM25前20 | 0.10 | 原画像 |
| soft_legacy_all | 全有效语料 | 0.10 | 原画像 |
| soft_aligned_k20 | BM25前20 | 0.10 | 对齐画像 |
| soft_aligned_all | 全有效语料 | 0.10 | 对齐画像 |

复用经过输入、源码和配置依赖核验的3个既有臂；新的2个臂对应18条排名。无法证明缓存有效时确定性重跑，报告实际计算数、缓存数及原因。新增ranker_id与上一轮ID建立显式映射。

所有臂沿用原评分公式、零分候选和全零查询规则；不偷偷添加“只返回BM25>0”。图表和指标中主矩阵固定45条；附加诊断单独计数。

### 7.1 归一化混杂检查

每(query,profile,candidate_setting)记录BM25和topic的归一化分母，并记录原始分数、最终分数。当前按候选集最大值归一化时，扩大集合可能改变topic分母。

若K20/all任一相关分母不同，主比较标记为“候选规模及归一化尺度联合变化”。额外计算该(query,profile)的全语料排名，沿用K20分母：

- 固定lambda和原始评分，不修改任何参数。
- 零分母沿用原实现行为并记录，不能临时补常数。
- 归一化值允许超过1，不截断；明确它是诊断分值。
- BM25全零查询继续执行原先零信号处理。
- 附加结果只解释混杂，不参加候选优选。

输出normalization_diagnostics.csv及normalization_counterfactual_runs.jsonl。无需触发的查询记录not_needed；触发数量动态报告，不强写18条。将固定分母诊断与K20、all主臂对照，说明返回变化来自新增候选还是共同候选的缩放变化。

## 8. 评价与预设决策

复用原评价定义及版本，不改变历史口径。报告每query、每topic、query宏平均：P@3、P@5、nDCG@3/5/10、Recall@10、返回数、零分返回比例。P@k分母固定k，缺额视作未命中；零相关查询的指标按既有明确规范处理，报告有效分母。

最终裁决、reviewer01、reviewer02三套视图分别计算，报告覆盖及相对BM25的逐查询差值、胜/平/负。三套视图不是三个独立重复，不将324文献对视为独立实验，不做显著性声明。

候选保留规则继续使用最终裁决视图：

1. query宏平均nDCG@5超过BM25（容差1e-12）。
2. P@3不低于BM25。
3. 每topic nDCG@5不低于BM25。

每个主软重排臂独立输出candidate_for_future_validation；报告两位原评审是否支持同方向。不因某臂通过就自动更换生产检索器，不将诊断性固定分母臂加入优选。

若全语料方案更好，只解释为当前小语料的探索性结果；不能推断大规模可扩展性。若无臂通过，保留BM25基线并结束，不改lambda、不删查询、不追加搜索。

## 9. 必要测试与验收

在已有测试上新增或复用：

1. 完整BM25排名的前20与原结果一致。
2. 固定BM25排序下，K增加候选集合满足包含关系。
3. 相同标签和相关性口径下，候选Recall不随K增加下降。
4. K>=N时覆盖全部有效文献；全零、并列、少于K均确定性处理。
5. 主矩阵45记录完整唯一，空结果保留；附加诊断另表。
6. K20两臂及BM25与上一轮顺序和指标一致，数值误差<=1e-12。
7. DCG贡献加和守恒，top5之外贡献为0。
8. 移除或篡改评分不影响检索输出与检索版本。
9. 候选规模、归一化规则及固定分母依赖进入语义哈希和缓存键。
10. 隔离目录实际加载交付快照模块，复现排名和指标。
11. 原归档执行前后checksum一致，原评分与来源未变。

依AGENTS.md在Linux运行doctor、完整测试及必要mock；本轮涉及配置/指标，完整mock检查18条件、20Agent、tick30、seed内fork一致和18分支事件重放。仅能在同一代码快照和配置下复用已有有效验收。记录实际测试数，不把118当作新目标。

开发依赖安装流量与离线实验调用分别记录；本轮实验网络、LLM、收费调用为0。不将历史外部AI辅助评审写成“从未使用AI”。

## 10. 交付目录及状态

新建outputs_stage_b_candidate_diagnostic/<run_id>/，至少包含：

```text
PLAN_FROZEN.json
INPUT_AUDIT.json
RUNTIME_MANIFEST.json
BASELINE_COMPARISON.json
bm25_full_ranking.csv
candidate_recall.csv
ranking_changes.csv
ranking_regression_analysis.csv
normalization_diagnostics.csv
normalization_counterfactual_runs.jsonl
ranker_runs.jsonl
metrics_by_query_ranker.csv
metrics_by_topic.csv
metrics_query_macro.csv
reviewer_sensitivity.csv
TEST_STATUS.json
REPRODUCTION_VALIDATION.json
DELIVERY_VALIDATION.json
STATUS.json
REPORT_ZH.md
reproduction/
CHECKSUMS.sha256
```

PLAN_FROZEN记录输入依赖、固定矩阵、指标、决策和条件触发规则。STATUS分别记录engineering_complete、linux_authoritative_validation、exploratory_analysis_complete、independent_human_validation=deferred_by_user、ready_for_scientific_claims=false和production_ranker_changed=false，按事实生成完成字段。

reproduction/包含语料、查询、画像、配置、标签来源与映射、代码快照及依赖说明，足以脱离开发目录离线复现；不含密钥、.venv。提供实际一键命令和源码版本，不依赖用户机器绝对路径。

CHECKSUMS覆盖所有交付文件但不含自身；ZIP自身哈希放在归档外，避免循环。测试ZIP可解压、内部清单一致，保留原归档不变。失败项如实写入验收JSON，不为凑all_passed跳过。

## 11. 中文报告与回传格式

REPORT_ZH.md简明回答：

1. 上一轮遗漏文献的BM25排名、分数是多少？
2. 全语料候选是否使它进入最终top5/top10？
3. 两个退化主题具体哪些文献被挤出或降位？
4. 候选扩展的变化是否涉及归一化尺度？固定分母诊断显示什么？
5. 哪些臂达到预设规则？是否应继续保留BM25作为基线？

附每臂主指标、每主题差值、原评审敏感性和Linux验收状态。说明所有标签仍为既有AI辅助评价，本轮是重复使用pilot的探索性诊断。

最终回复用户：代码修改摘要、实际测试数、Linux状态、关键结果差值、失败项、ZIP和报告路径、复现命令。用户回传时优先提供完整ZIP和这段摘要，供下一轮判断。

## 12. 完成与停止条件

固定实验、必要验收、报告和归档完成后停止。工程完整但算法无提升是有效交付；Linux不可用或输入缺失时，完成可做的工作并准确标记未完成部分。不安排新人工标注，不自行扩大语料、增添排序器、反复调参或部署。
