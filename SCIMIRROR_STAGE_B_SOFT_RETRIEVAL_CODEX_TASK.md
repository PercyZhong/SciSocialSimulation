# SciMirror Stage B 下一轮执行文档：检索软重排、阶段诊断与跨平台冻结

日期：2026-09-24。执行者：VS Code 中的 Codex。

## 1. 直接执行任务

请在当前 SciSocialSimulation 仓库完成本文要求的代码、测试、离线实验和中文报告。先读 AGENTS.md，检查 git status，保留用户未提交修改。不要只提交计划。独立人工核查已由用户明确延期，不应成为本轮执行或工程验收的前置条件。继续使用现有 AI 辅助评审记录作探索性分析；不补造人工评分、不修改原标签来提高结果。

本轮目标是回答：旧排序器在哪个环节丢失相关文献？取消硬主题过滤、改为 BM25 召回后软重排能否改善？改善是否依赖主题语义修订？同时解决 LF/CRLF 导致冻结版本不同的问题。

不启动收费 API、LLM 评审或大规模参数搜索。不扩大语料规模，不自动替换生产 Agent 的检索路径。完成当前离线工作后交付结果，不等待人工复核。

## 2. 已核对的代码与实验基线

仓库：https://github.com/PercyZhong/SciSocialSimulation

本文编写时 GitHub HEAD：`cce209bea9b585e0a767cbb0a812b57fce1c0af3`。若本地版本较新，记录差异并适配，不强制 reset。

已核对的实际入口：

- `execute_stage_b_retrieval.py`：现有 Stage B 命令入口。
- `scimirror/stage_b_pilot.py`：真实执行的 freeze、verify_freeze、pool、标注导入与分析。
- `scimirror/stage_b_retrieval.py`：保留旧函数并在末尾导入 pilot 实现；不要只修改被覆盖的旧函数。
- `scimirror/retrieval_evaluation.py`：评价指标。
- `scimirror/v03_retrieval.py`：Stage A 检索实现。
- `configs/stage_b_retrieval_small.json`、`tests/test_stage_b_pilot.py`、`tests/test_stage_b_retrieval.py`。

当前 pool 对旧排序器先调用 Stage A 主题门控，再对返回集合计算 evidence_score + query_weight × 归一化 BM25；BM25 基线直接对所有 canonical 文献排序。当前 freeze/verify_freeze 用文件原始字节哈希，源文件也在依赖中。

最近归档参考名：`stage_b_linux_exploratory_488b5c60_20260922(1).zip`（文件可能无 `(1)` 后缀）。优先使用用户本地已有归档或对应输出目录，不凭文件名认定内容。寻找包含 FROZEN_PILOT.json、REVIEW_PROVENANCE.json、corpus.jsonl、queries.csv、private/ 的完整目录，校验 CHECKSUMS。

已报告基线：36 篇文献、9 条查询、324 个全语料查询—文献对、648 条双评、45 条裁决。原始一致率 0.861111，线性加权 Kappa 0.758048。以下数值只作为核对参考，以原始记录重算为准：

| 排序器 | P@3 | nDCG@3 | P@5 | nDCG@5 |
|---|---:|---:|---:|---:|
| BM25 | 0.851852 | 0.796075 | 0.777778 | 0.795935 |
| Stage A closure | 0.592593 | 0.427208 | 0.422222 | 0.361173 |
| Stage A repair | 0.370370 | 0.244793 | 0.311111 | 0.244565 |

两个旧排序器在 science_evaluation 的三条查询上均为空。这是本轮重点诊断对象。现有评审为 AI 辅助、人工抽查和提交，不等同独立人工金标准。归档的 ready_for_scientific_claims=false 必须保留。

## 3. 本轮代码边界与文件布局

建议新增以下模块；若已有等价模块，复用并在报告列映射：

| 文件 | 责任 |
|---|---|
| `scimirror/stage_b_improvement.py` | 编排导入、冻结、实验、分析和验收 |
| `scimirror/retrieval_soft_ranker.py` | BM25 候选召回与软重排，不访问评分文件 |
| `scimirror/retrieval_stage_trace.py` | 逐文献各阶段追踪及事后损失归因 |
| `scimirror/semantic_fingerprint.py` | 版本化规范序列化、语义哈希 |
| `execute_stage_b_improvement.py` | 本轮独立 CLI，一键离线入口 |
| `configs/stage_b_improvement.json` | 固定实验参数，不含机器绝对路径 |
| `data/stage_b_query_routes_v1.json` | 仅用于检索实验的查询路由 sidecar |
| `data/retrieval_pilot_profiles_v1.json` | 检索专用新主题定义，保留旧文件 |
| `tests/test_stage_b_improvement.py` | 实验不变量、标签隔离、基线复现 |
| `tests/test_semantic_fingerprint.py` | 换行、迁移、变更识别 |

历史归档只读；新输出写到 `outputs_stage_b_improvement/<run_id>/`。保持旧 CLI 和旧格式读取兼容。禁止覆盖旧 freeze、评分历史或修改历史 checksum。

## 4. P0：拆分主题语义，避免混淆算法与任务变化

保留 queries.csv 的原 query_id、topic_id、query_text、intent、split 与来源；不重写查询或其意图。原 science_evaluation 画像偏向科研 Idea/提案评价，当前真实查询涉及同行评审和计量评价，应显式记录语义差异。

新 sidecar 至少包含 query_id、original_topic_id、retrieval_topic_id、route_reason、route_source、route_version。按真实 query_id 配置，不依赖 CSV 行号。

science_evaluation 的三个查询意图分别路由到：

- `peer_review_reliability`：论文/基金同行评审的一致性与可靠性。
- `peer_review_bias`：单盲/双盲、身份或性别偏差。
- `bibliometric_evaluation`：计量指标用于科研评价的适用性与局限。

memory 与 science_retrieval 暂用原画像，不新增针对具体文献的关键词。新画像仅根据查询文本和定义撰写，记录 `route_source=developer_proposed_from_query_intent`、`human_validated=false`。不看标签、文献标题或返回结果补词。冻结画像后再跑比较；不可看到结果后迭代修词。

禁止更改全局 Agent topic taxonomy；sidecar 仅供本轮检索实验。画像改动作为单独消融因素，不与算法改动混合解释。

## 5. P0：新增 BM25 召回后的软重排

固定参数：BM25 k1=1.2、b=0.75；候选 K=20；输出 k=10；lambda=0.10；并列按 paper_id 升序。该 lambda 是本轮预设，不是 calibration 结果；不得遍历调参再挑最好值。

对相同 canonical 文献、相同时间截止和相同查询：

1. 复用现有 bm25_scores，按 (-score, paper_id) 取前 min(20,N) 篇。
2. 为严格隔离重排效果，主实验候选保留 BM25 零分项，行为与旧基线一致；单列零分比例和 zero_signal 查询。零分不代表相关。
3. 令 B(d)=BM25(d)/max_pool_BM25；若最大分数为0，则 B=0。
4. 使用已有、不访问标签的主题评分代码取得有限非负 T_raw；显式记录其定义。T(d)=T_raw/max_pool_T_raw；最大值为0时 T=0。若原评分可为负，先 max(0,T_raw)，记录原值。禁止凭空构造得分。
5. 非零信号查询按 S(d)=B(d)+0.10*T(d) 排序。没有硬主题阈值、没有主题拒绝、没有字段或 memory 加分。
6. BM25 全零查询直接保持 BM25 确定性顺序并标记 zero_signal，不允许主题画像伪造查询召回成功。
7. 主比较不额外施加近重复过滤：输入 canonical 集已由冻结 family_map 决定。旧排序器内部既有去重保持不变并追踪。任何新增去重或正分过滤必须另设实验，不混入主臂。

必须存在 lambda=0 对照，并与原 BM25 的 top10 ID、顺序及返回数完全一致；分值尺度可不同。未知画像令 T=0 并输出告警，不能丢弃文献。

不要把归一化得分称作概率或质量。不得宣称软重排必然优于 BM25。

## 6. 固定实验矩阵

一次运行 9 查询 × 6 臂 = 54 条运行记录；即使为空也保留一条。冻结输入哈希和参数后再运行。

| ranker_id | 算法 | 画像 | 目的 |
|---|---|---|---|
| bm25_lexical | 现有 BM25 | 不用 | 原始基线 |
| legacy_closure | 原 closure 管线 | 原 closure | 历史复现 |
| legacy_repair | 原 repair 管线 | 原 repair | 历史复现 |
| soft_lambda0 | 新召回+lambda=0 | 原 repair | 实现等价对照 |
| soft_legacy_profile | 新召回+lambda=0.10 | 原 repair | 只改算法 |
| soft_aligned_profile | 新召回+lambda=0.10 | 新路由画像 | 只改画像的增量 |

主比较：soft_legacy_profile 对 BM25；算法相对旧 repair 的变化同时报告。画像增量：soft_aligned_profile 对 soft_legacy_profile。不能把最终臂的收益全部归因于软重排。

这9条查询已多次用于分析，标记 `evaluation_split=exploratory_reused_pilot`；本轮不是留出测试或正式确认实验。无需重复人工打分。324对全语料标签可覆盖新排序，前提是 query 文本、意图、文献内容与标注范围没有变化。

## 7. P0：逐阶段损失归因

排序代码输出不含标签的 `retrieval_trace.jsonl`；评价阶段再关联既有评分，生成 `retrieval_trace_labeled.jsonl`。两阶段物理分离，不向检索函数传 qrels、reviewer 分数或裁决。

对每个 query × ranker × canonical paper 保留一行，当前规模 9×6×36=1944行。字段至少包括：query_id、ranker_id、paper_id、family_id、original_topic_id、retrieval_topic_id、bm25_raw、bm25_rank、topic_score_raw、topic_score_normalized、gate_threshold、gate_pass、candidate_rank、dedup_kept、rerank_score、final_rank、returned、first_exclusion_reason、stage_path。对不适用阶段填 null，不冒充通过。

旧算法按实际执行顺序记录：输入限制→主题门控→候选截断/去重（遵循实际代码顺序）→查询重排→top10；新算法：输入限制→BM25 top20→软重排→top10。不能把 BM25 top20 的诊断强加为旧算法真实步骤。

每个相关文献（最终评审 grade>=1）定位首次排除原因：时间/有效性、family canonical、主题门控、候选截断、去重、最终topk。canonical之前排除的原始文献另表，不混入1944行或重复计数。

输出 `loss_by_stage.csv`：进入数、保留数、丢失数、进入相关数、丢失相关数、条件损失率、相对全语料相关数的累计保留率；零分母写 null 和原因。每条管线的互斥首次丢失数+最终保留数必须守恒。标签未知不能按不相关处理。

science_evaluation 每条查询给出可核对的失败文献ID、旧门控分数/阈值和首次排除阶段；避免仅用宏平均解释失败。

## 8. P0：语义冻结与标签迁移

保留 raw_sha256 作归档字节完整性检查，新增 semantic_sha256 作逻辑内容版本。禁止把旧归档改成 CRLF 以迁就版本。

实现带 schema_version 的规范化：

- UTF-8，允许文件BOM并显式去除；JSON键排序、紧凑序列化；拒绝重复JSON键、NaN、Infinity。
- JSONL/CSV 按明确 schema 解析类型；数值字段 year/rank/grade 是整数，不接受无意义类型漂移；浮点参数按固定规范输出，禁止直接字符串拼接。
- corpus按paper_id、query按query_id、family_map按paper_id排序；重复主键拒绝。CSV列顺序、记录换行、JSON缩进变化不影响语义哈希。
- 排名列表、事件序列、评分修订历史的顺序具有意义，不可统一排序所有数组。
- 文本字段不 trim、不小写化、不改标点或Unicode组合形式；仅允许声明的内部 CRLF/CR→LF 换行归一化。字段实质变化应改变对应哈希。
- 源代码保留raw哈希，同时另算只规范换行的source_sha256；不能用Python AST丢弃有意义源码来规避版本变化。

分别生成 corpus_hash、query_hash、family_hash、ranker_config_hash、profile_hash、source_hash、annotation_scope_hash、labels_hash。retrieval_run_id 不依赖标签；evaluation_run_id 包含 retrieval_run_id 和 labels_hash。运行时间、绝对路径和平台信息记录在manifest，不作为语义实验身份。

迁移在新目录执行，输出 `annotation_migration.csv`：old_pool_version、old_blind_id、new_pair_id、query_id、paper_id、query_semantic_hash、document_semantic_hash、scope_hash、status、reason。

复用标签必须逐对匹配 query_text+intent、文献标题/摘要及评价所见内容、标注问题/量表和评审身份；画像变化仅改变检索，不改变问题范围时允许复用。保留原评分值、rationale、rated_at、revision、评审来源和旧ID。reviewer身份或范围变化不可静默迁移。新ID与历史ID通过映射关联，不篡改历史记录。

内容不一致时标为 unmatched，不填0、不调用模型补分；可以完成诊断，但受影响指标标 incomplete。本轮独立人工核查仍为 deferred，不因此伪造完成。

## 9. 探索性评价与决策

复用既有指标定义并输出定义版本，避免同名异义。P@k分母固定k，缺少返回按未命中；相关为grade>=1，另报严格grade=2敏感性。nDCG使用 gain=2^grade-1、折扣log2(rank+1)，IDCG来自冻结全语料标签。无相关文献的查询单列，不静默填0进入宏平均。Recall@k用全语料已解析相关文献为分母；未知标签存在时禁止输出伪完整Recall。

输出每query、每topic、9query宏平均，k=3/5/10。附返回数、空返回率、零信号率、top20相关召回率、top10相对BM25的Jaccard/换位比例。family口径复核，不把同一family重复算命中。

分别使用三套既有标签视图：最终裁决结果、reviewer01原始结果、reviewer02原始结果。输出三个视图的排序与成对差值。无法解析的unsure保留未知并报告覆盖。三套视图不是三次独立重复。

主终点预设为 query宏平均nDCG@5；约束指标P@3、各topic nDCG@5、空返回率。只报告9query逐条配对差和胜/平/负，不做“显著优于”结论、不把324对当独立样本、不新增bootstrap显著性门槛。

探索性保留规则（不是科学验收）：主终点超过BM25、P@3不低于BM25、各topic nDCG@5不低于BM25，才标 `candidate_for_future_validation=true`；比较浮点容差1e-12。另列两位原评审视图是否同向。未满足则保持BM25为后续候选基线，不调lambda、不删查询。无论胜负，生产引擎默认路径不自动改变。

## 10. 状态字段与验收

新增状态明确区分：

```json
{
  "engineering_complete": true,
  "exploratory_analysis_complete": true,
  "independent_human_validation": "deferred_by_user",
  "review_scope": "exploratory_ai_assisted_review",
  "evaluation_split": "exploratory_reused_pilot",
  "ready_for_scientific_claims": false,
  "candidate_for_future_validation": false,
  "production_ranker_changed": false
}
```

以上是格式示例，布尔值按执行事实生成，不能直接照抄true。engineering_all_passed与algorithm_improved分开。独立人工延期不影响工程通过，但不能提升科学声明状态。

必须验证：

1. 三个历史臂的ID顺序、返回数及指标与归档一致（指标误差<=1e-12）；差异列明并阻止“基线复现通过”。旧源码变化时用只读旧版本兼容复现，不删除freeze校验。
2. lambda0与BM25返回完全一致；未知画像、全零query、候选不足、并列分数均有确定行为。
3. 新软重排不硬过滤；输出只能来自其BM25候选集；打乱输入迭代顺序不改变排名。
4. 54运行记录、1944追踪行唯一完整，所有空结果保留；损失守恒。
5. 标签隔离测试：移除/篡改qrels只影响评价、不影响检索ID与排序；新的路由/画像冻结不依据评分。
6. 旧324对迁移逐条核对；值、历史、来源未改变。迁移失败必须显式列出。
7. LF/CRLF、JSON缩进/键序、CSV列序等价时语义版本一致，raw哈希允许不同；改查询含义/文献/参数应改变对应版本；修改标签仅改变评价版本。
8. 旧源档checksum执行前后不变；独立目录离线复现排名与指标一致。
9. 真实Linux与Windows测试分开标；只有Linux模拟换行测试时写 `windows_native_test=not_run`，不能宣称Windows原生通过。
10. 独立人工核查deferred且科学声明false；网络、LLM、收费调用均为0（仅指本轮程序执行，不否认历史外部AI评审）。

按AGENTS.md运行全部测试和一轮完整mock，因为本轮涉及配置、持久化与指标。验证18分支、20Agent、tick30、seed内fork一致和事件重放。不要反复运行多轮synthetic搜最佳结果。

## 11. 执行入口、输入发现与失败策略

新增以下CLI（这些是要实现的命令，不代表当前已经存在）：

```bash
python3 execute_stage_b_improvement.py run --config configs/stage_b_improvement.json --source-run /path/to/frozen_stage_b_run --output outputs_stage_b_improvement/run_01
python3 execute_stage_b_improvement.py validate --run-dir outputs_stage_b_improvement/run_01
python3 execute_stage_b_improvement.py reproduce --run-dir outputs_stage_b_improvement/run_01 --output outputs_stage_b_improvement/reproduced_01
```

run负责核查输入→复制只读基线快照到新目录→语义迁移→冻结方案→54次排名→标签关联与指标→生成报告。各步可恢复，cache key包含实际语义依赖，不可复用过期结果。不覆盖已有不兼容run目录。

允许 --source-archive 输入zip：安全解压拒绝路径穿越/符号链接逃逸，按文件集合定位唯一run。无输入参数时仅搜索仓库规定outputs目录；发现多个不等价完整档则列选择清单，不擅自挑优异结果。

若完整归档不在本地，仍完成代码、自动测试、无真实标签fixture的流程测试和README；生成missing_inputs.json，明确真实pilot未运行。不得用仓库示例或合成数据替代36/9真实pilot并声称完成。只有这个实际缺失才需要用户提供数据；不索要新人工标注。

标准Linux操作：

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py doctor --config configs/mock.json
python run.py test
python run.py run --config configs/mock.json
```

已有可用.venv则复用。依赖安装如需网络，单列开发环境安装流量；实验执行网络必须关闭，程序不得请求文献服务或模型。标准库优先，不引入向量数据库、GPU框架或外部重排API。

## 12. 交付清单与停止条件

输出目录必须包含：

- PLAN_FROZEN.json：矩阵、固定参数、主终点、决策规则、输入和代码哈希。
- RUNTIME_MANIFEST.json、INPUT_AUDIT.json、REVIEW_PROVENANCE.json。
- BASELINE_REPLAY.json、SEMANTIC_HASH_VALIDATION.json、annotation_migration.csv。
- ranker_runs.jsonl、retrieval_trace.jsonl、retrieval_trace_labeled.jsonl、loss_by_stage.csv。
- metrics_by_query_ranker.csv、metrics_by_topic.csv、metrics_query_macro.csv、paired_differences.csv。
- reviewer_sensitivity.csv、science_evaluation_failure_analysis.csv。
- STATUS.json、TEST_STATUS.json、MOCK_REPLAY_VALIDATION.json、DELIVERY_VALIDATION.json。
- 中文 REPORT_ZH.md：主要结果、具体失败阶段、算法效应与画像效应分开、是否保留候选、局限及下一步。
- reproduction/：冻结语料/查询/画像/配置/评分来源与映射、必要代码版本材料、requirements和一键复现说明；不包含密钥或.venv。
- CHECKSUMS.sha256与归档zip。校验表覆盖归档内所有交付文件（不含校验表自身）；归档自身SHA-256放在归档外，避免循环哈希。

测试日志记实际通过数，不把102写成新的固定目标。文档解释每个失败验收项；工程通过不要求soft必须胜出。如果不如BM25，如实报告并停止调参。

完成后向用户简报：改了哪些文件、基线能否复现、软重排是否改善、画像贡献多少、丢失发生在哪、语义迁移是否成功、独立人工仍延期、产物目录与执行命令。不要自动push、部署或调用收费模型。

本轮停止在“离线检索机制可解释、版本可复现、候选方案有探索性比较”。后续可另开新的未见查询/文献评估；独立人工审查由用户以后安排。
