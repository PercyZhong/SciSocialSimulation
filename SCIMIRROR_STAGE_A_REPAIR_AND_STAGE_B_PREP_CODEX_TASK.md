# SciMirror：阶段 A 有限修正与阶段 B 真实文献准备

版本：1.0 ｜ 日期：2026-09-15

交付类型：交给 VS Code Codex 的完整执行规格。本文未实施仓库修改，也不包含已运行的新实验结果。

仓库：https://github.com/PercyZhong/SciSocialSimulation

本次在线核对提交：`33fd6ecd7fa2fb9f7f029c6566ade50a324579cb`。

## 0. 直接执行指令

请阅读本文件和当前仓库的 AGENTS.md，直接完成代码修改、Linux 离线实验、验收、中文报告和归档。不要只回复计划。

本轮完成两部分：

1. A-Repair：旧语料回归、真实校准、gold 语义审计、memory 接线、挑战集独立评价。
2. B-Prep：真实文献导入、人工查询与盲标注、冻结检索比较的可执行工具。没有真实数据或人工标注时，完成工具和模板并准确记录待输入状态。

保留未提交改动、历史输出和历史配置。当前 HEAD 如有更新，核对差异后复用已正确实现的部分；不得强制 reset 到本文提交。记录 actual_head、代码差异、数据及依赖哈希。

Linux 为权威平台，Python ≥3.11。通过 VS Code Remote SSH/WSL 在 Linux 执行。正式依赖沿用仓库，优先标准库；不引入向量数据库、GPU 或新 Agent 框架。

本轮默认全部离线，不调用 LLM、收费 API，不自动爬取文献。完成真实文献的本地导入工具即可。不要输出或要求明文密钥，不自动推送或合并远程分支。

保持 reward、recognition 公式、合作人数、退出规则、项目投入及产出统计定义不变。检索修复通过新增模式启用，旧模式保持可重跑。

不得通过复制正例、删除难例、修改测试 gold 或事后降低阈值获得通过。有限修正后即交付真实结果；未通过也要输出报告与失败归档。

## 1. 问题依据与历史结果

已核对以下源码：

- `scimirror/stage_a_closure.py::prepare`：从 positive_phrases 构造新增文献，混入旧扩充语料。
- `stage_a_closure.py::calibrate`：直接写入三行固定 trial，没有执行检索测量。
- `scimirror/v03_retrieval.py::retrieve_stage_a_semantic_guarded`：接收 memory_terms，但未使用；field context 影响候选截断，未进入最终分数。
- `configs/stage_a_closure.json`：expanded 指向 `data/stage_a_closure_v3/corpus_expanded_closure.jsonl`，不是补充实验的旧扩充语料。
- `scimirror/v02_engine.py::step_v02`：已有少于两篇证据时跳过生成的路径，保持并回归验证。

上一轮独立审计得到的参照值，实施时必须重新计算，不能硬编码成实测输出：

| 条件 | 容量覆盖率宏平均 | science_evaluation |
|---|---:|---:|
| 旧扩充语料 × stage_a_fixed | 0.907407 | 0.037037 |
| 旧扩充语料 × closure semantic_guarded | 0.472222 | 0 |
| Closure 混合语料 × closure semantic_guarded | 1.000000 | 1.000000 |

Closure 混合语料中新检索器的 972 个返回槽位全部来自新增 closure 文献。旧语料上 agent_tools、learning_exploration、science_collaboration、science_evaluation、science_retrieval 各 27 次返回空。

这些失败既可能是检索漏召回，也可能是旧标签与正文语义不符。尤其 science_evaluation 的原主题定义是 Idea evaluation，普通模型 performance evaluation 不自动相关。

## 2. 修改架构

建议新增路径如下；若已有等价功能，复用并在 CODE_CHANGE_REPORT.md 给出对应关系。

| 路径 | 职责 |
|---|---|
| `execute_stage_a_repair.py` | A-Repair 一键入口 |
| `scimirror/stage_a_repair.py` | 数据集分离、冻结矩阵、汇总 |
| `scimirror/retrieval_calibration.py` | 真实执行 calibration 与选择参数 |
| `scimirror/retrieval_evaluation.py` | 独立指标、gold 版本、质量验收 |
| `scimirror/retrieval_gold_audit.py` | 文献语义审计、争议与修订提案 |
| `scimirror/v03_retrieval.py` | 新增 `stage_a_repaired_v1`，保留两种旧模式 |
| `scimirror/corpus.py` | 新模式分派 |
| `configs/stage_a_repair.json` | 预设实验与阈值 |
| `data/retrieval_topic_profiles_repair_v1.json` | 版本化检索描述 |
| `execute_stage_b_retrieval.py` | 真实文献本地试验入口 |
| `scimirror/stage_b_retrieval.py` | 导入、去重、盲标注、真实检索比较 |
| `configs/stage_b_retrieval_pilot.json` | 3 主题小试验配置 |
| `tests/test_stage_a_repair.py` | 指标、隔离、恢复及回归 |
| `tests/test_stage_b_retrieval.py` | 本地导入和标注工具测试 |

实现模块可适度合并，但实验逻辑不要塞入单个无边界的大函数。测试沿用 unittest，不要求切换测试框架。

## 3. 数据集必须分开，不再合并验收

创建只读 dataset registry，至少包含以下四类。路径从已核实的配置解析，不凭文件名前缀猜测。

| dataset_id | 来源 | 用途 |
|---|---|---|
| `original_legacy` | data/demo_papers.jsonl | 历史兼容性与供给不足描述 |
| `supplement_unchanged` | configs/stage_a_supplement.json 中的原 expanded 及 gold | 主要旧语料回归；必须无新增 Closure 文献 |
| `closure_challenge_only` | Closure corpus 相对 supplement 的实际新增 ID 集 | 规则正反例测试，单独报告 |
| `closure_mixed_legacy` | 原 Closure 混合语料 | 历史复现、来源贡献诊断；不得替代旧语料验收 |

每类记录原始文件 SHA-256、年份过滤后的内容哈希、文献数、family 数、qrels 版本、标签来源和是否被开发者看过。

验证旧 90 条文献逐条正文及旧 qrels 均未变化。发现历史 qrels 是从标签重建且内容不同，要报告差异，不宣称保留原标注。

challenge-only 不重新生成正例。使用已冻结的 44 篇唯一文献/45 条主题标注作为历史参照，数量以复核为准；按 `(query_id,paper_id)` 索引，避免一个共享文献的多主题标注被 dict 覆盖。

每类独立输出指标和质量状态。保留来源分解：旧文献/新增文献返回数、唯一 ID 数、槽位占比、相关 family 数。不得只给混合语料宏平均。

## 4. Gold 语义审计：修订可追溯，争议不能消失

为五个空返回主题涉及的所有旧正标签文献生成逐条审计表，同时检查明显退化的 communication、planning、reward。保留原文标题和摘要。

字段至少包括：

```text
topic_id, paper_id, gold_family_id, old_grade, topic_definition
title, abstract, supporting_excerpt, contradicting_excerpt
proposed_grade, review_status, reviewer_type, reviewer_id
reason, old_qrels_hash, proposed_revision_version
```

review_status：`supported / unsupported / ambiguous / pending_human`；明确这是 Codex 的自动审计提案，不能填充虚构的人类 reviewer。

处理规则：

1. 旧 qrels 永远保留，所有旧指标照常报告。
2. 自动审计依据正文与冻结主题定义，不能依据检索器是否选中来决定相关性。
3. 生成 revision proposal，默认不替换正式 qrels。
4. 如运行提案 qrels 的探索性分析，单列 `provisional_qrels`，不得用于绕过旧回归失败并把 overall 改为 passed。
5. 人工导入裁决时必须包含 reviewer_id、依据、时间、旧新版本。无裁决的争议不得自动排除在分母外。
6. 文献多主题、family 共享或同一文献多版本，都允许表达；不强制一篇对应唯一研究主题。

对于 science_evaluation：不为提高旧 gold 覆盖而允许通用 evaluation 单词通过。标签语义有争议时，正确输出 `needs_gold_review`。这不阻碍工具交付，但阻止宣称语义验收完成。

## 5. 检索修复：有限、透明、可回归

### 5.1 版本与边界

保留 `stage_a_fixed` 和 Closure `stage_a_semantic_guarded` 的行为；新增 `stage_a_repaired_v1`。若提取共享函数会改变旧模式，先冻结旧实现，按快照测试确保行为未变。

检索 profiles 与 `TopicModel`/recognition 分开。不要修改 data/topics.json 来适配检索；同语料、同状态下三种 ranker 的 recognition 快照必须一致。不同语料的 attention 分布可以不同，但不可据此把跨语料差异全部归因于排序。

### 5.2 语义修复要求

- 对旧正文中合理的 tool selection、paper retrieval 等表达补充主题定义支持的别名/概念组，不添加具体 paper ID、编号或模板长句。
- 对较宽主题如 exploration、reward，定义需要的研究对象与方法上下文；对语义不明确的短文本允许拒答，记录原因。
- 短语匹配采用 token 边界，不使用简单子串包含。避免 tool 命中 tooling 等非预期形式。
- 词形归一化采用显式可解释规则或冻结映射，避免对所有 s 结尾单词直接截尾。
- evidence gate 只读取 title/abstract；领域、memory、policy 不得救回 gate 失败文献。
- 区分同一句中的完整概念与跨段落拼凑。匹配窗口、短语、上下文约束写入配置和审计。
- 不增加 IDF、embedding 等第二套复杂机制，除非现有透明规则确实无法处理已明确的表达，并记录必要性。本轮最多一次设计修正和一次有限参数校准，不进行测试集调参循环。

### 5.3 恢复 memory，但只在合格池内重排

明确区分 semantic memory (`memory_terms`) 与阅读历史 (`read_texts`)。本轮选择恢复前者，并提供开关。

候选池构造：先对全语料执行主题证据 gate，再按 evidence score、稳定 paper ID 取前 20。候选池不依赖 memory、field 或 policy，便于检查干预隔离。基线保持其原候选规则，不强求跨 ranker 相同池。

在共同合格池内，定义：

```text
E(d) = 已归一化的主题 evidence score，范围 [0,1]
F(d) = field 匹配分数，范围 [0,1]
M(d) = 语义 memory 概念与文献正文的匹配分数，范围 [0,1]
R(d) = (1-beta_field-beta_memory)*E(d)
       + beta_field*F(d) + beta_memory*M(d)
P(d) = w_novelty*exploration(d) + w_recognition*attention(d)
Final(d) = alpha*R(d) + (1-alpha)*P(d)
```

默认 `alpha=0.75, beta_field=0.05, beta_memory=0.10`，作为待真实校准的候选，不称已验证最优。保证两个 beta 非负且和小于 1。

memory 关闭或空值时，令 beta_memory=0，把该权重归还 E。未知 memory ID 不可静默视为已知主题；记录解析状态。展开 memory 使用冻结的主题描述/概念词，不读取测试 qrels。

按 Final、E、稳定 ID 排序并执行原去重约束。top_k=3，允许短缺。不改变 policy 的 0.5/0.5、1/0、0/1 权重。

输出逐文献 E/F/M/P/Final、effective weights、matched evidence、gate reason、pool membership、selected rank。query_id/decision_id 包含语料内容、topic、field、memory、read-history、policy、配置与版本，避免不同查询上下文复用同一个 ID。

### 5.4 Memory 验证

- 同一主题/field/阅读历史/policy，切换 empty/related/unrelated，只允许重排分数改变；gate 和共同池不变。
- memory off 下，三种 memory 输入的分数和结果均一致。
- memory on 下，固定手工特征表验证公式，并用生产特征提取至少一个合理场景证明 M 和 Final 确实变化。
- 不要求每次选中集合变化；记录 feature/score/order/set 四级响应及分母。
- 修改测试 qrels 不改变 M 或排序。
- 通过真实 Corpus.retrieve_v02 和 step_v02 检查新模式、memory 开关确实传入，不能只测试工具函数。

## 6. 真实校准与冻结

删除新流程中的固定 trial 测量值。历史归档不改写，新增 `CALIBRATION_RECORD_CORRECTION.md` 说明旧 CSV 实际是预设方案。

预先列出最多 6 个候选配置，例如：

```text
(minimum_relevance, window, beta_memory)
(0.8,12,0.05), (0.8,12,0.10), (0.8,18,0.05)
(0.8,18,0.10), (0.9,18,0.05), (0.9,18,0.10)
```

若评分尺度改变，可在运行 calibration 前一次性调整网格并记录原因。threshold=0.9 可能拒绝 concept-group 正例，必须实际测量。

每个 trial 必须真实调用 production retriever。保存 case 级 selected IDs、分数、gold 版本、负例 gate 结果、调用次数与耗时。只有静态方案时字段应为 `measurement_status=not_run`，数值 null，不能填 0。

使用现有 calibration families/明确的 calibration negatives；隔离 test family，避免近重复进入两个 split。旧测试已被开发者看过，必须标记 `development_seen_regression`，不宣称未见泛化。

选择规则先冻结：必须满足 calibration 负例约束；再最大化按主题宏平均 F1（空返回按 0）；并列时选择 beta_memory 较小者，再按 trial_id。若无配置满足约束，记录 no_feasible_trial，不自动扩大搜索。

选择后写 FROZEN_PROTOCOL.json，冻结配置、profiles、语料、qrels、calibration 日志及实现依赖哈希。正式评估只能读取冻结协议，不重新生成数据或自动重选参数。

## 7. 实验矩阵与可解释比较

### E0：复现缺陷

在 supplement_unchanged 上比较 fixed 与 Closure guarded，各 324 次检索。复算历史均值及 5 个空返回主题，差异写明，不把参照值当断言常量。

E0 结果若与正式矩阵的相同 case 完全同协议，可复用，不重复计费/计数。

### E1：主要回归矩阵

```text
108 状态 = 12 topics × 3 fields × 3 semantic-memory conditions
3 rankers = fixed / closure_guarded / repaired_v1
3 policies = balanced / novelty / recognition
2 corpora = original_legacy / supplement_unchanged
总逻辑 case = 1944
```

read_history 固定为空，使用相同状态与政策输入。报告统计单位为确定性诊断状态，不是独立社会世界。

### E2：挑战集单独评价

对 challenge-only 的每条 `(topic,paper)` 标注评估 evidence gate，按 positive/negative/context/synonym/shared/duplicate/year 分组。所有负例均展示实际分子分母；不能只用负例通过数而不报告正例拒绝数。

对 closure_mixed_legacy 可复用旧结果，但仅在输入、代码、配置和 recognition 哈希完全相同的情况下复用。新 ranker 在此语料上的 324 次检索用于来源贡献与分数诊断，单列，不参与旧语料回归门槛。

新挑战集如由同一开发者生成，仅标记规则测试。本轮不再自动制造新的“独立 gold”。

### E3：机制控制

复用原 Policy 3 场景 × 3 histories，运行 3 rankers × 3 policies，共 81 个启用 case；关闭 policy 的 empty-history 控制为 3 场景 × 3 rankers × 3 policies，共 27 个 case。若现有场景不足以表达 tradeoff，只记录原因，不修改 fixture 使差异必然出现。

Memory 控制使用 3 个事先定义的场景 × 3 memory × 3 policies × on/off，共 54 个新 ranker case。先冻结场景，不根据正式结果选择“漂亮案例”。

### E4：生产引擎与仓库回归

复用 Closure 的 8 个 0/1/2/3 边界场景和 20 Agent/12 tick smoke，但配置切换为 repaired_v1。保留事件、可见引用、能量、草案、项目和回放检查。

按照 AGENTS.md 运行 doctor、test 和完整 18 分支 mock，并验证 final states、共同 fork 哈希及事件回放。不要把旧引擎测试通过当作新模式接线已验证。

## 8. 指标、退化门槛与状态

独立评估器只读取结果 ID、语料可用性、family 映射与 qrels，不复用排序器的相关性判断。

G = 截止年前、当前语料实际存在的相关 family；S = 返回文献映射得到的 family。

```text
capacity_coverage = |S ∩ G| / min(k, |G|)
family_recall = |S ∩ G| / |G|
document_precision = 返回相关文献数 / 实际返回文献数
candidate_family_recall = 前20候选中相关family数 / |G|
```

G 为空：coverage/recall=null，记录 no_gold_supply；有供给且空返回：coverage/recall=0，precision=null，同时记录 empty。不得通过丢弃 null 行隐去空返回。重复评价用独立 family，不能用算法自己的簇作为唯一真值。

新增：逐主题、逐 memory 指标，正例 gate recall，负例 false-positive rate，old/new 来源占比，错误类型表。gold 未标注必须是 unjudged，不能默认 grade=0。

以下是预设工程门槛，正式测试前冻结：

| 门槛 | 要求 |
|---|---|
| 完整性/指标正确性 | 预期组合唯一、无漏项、复算一致 |
| 旧语料回归 | 原本 coverage≥0.8 的主题，新版降幅≤0.05；Precision 降幅≤0.05 |
| 充分供给时的空返回 | 人工确认或既定无争议 gold 有≥3 family 时，不得出现新增全主题空返回 |
| 负例与正例 | 冻结明确负例 gate 通过=0；明确正例 gate recall≥0.90；争议单列 |
| Gold 隔离 | 改变评估文件不改变生产排序 |
| Memory | off 一致；on 公式正确且至少一例生产分数响应；不强制集合变化 |
| 冻结/恢复 | 实现或输入改变拒绝复用；相同协议重复运行无重复 case |
| 引擎/复现 | 新模式验证、18 分支回归和独立目录重跑通过 |

旧 gold 原始口径下的退化必须输出 failed。若有语义争议，同时输出 needs_gold_review，但不能用这个标签把 failed 改成 passed。经真实人工裁决后可追加新口径结果，原失败记录仍保留。

建议结果结构：

```json
{
  "execution_status": "completed",
  "engineering_status": "passed",
  "legacy_regression_status": "failed",
  "gold_review_status": "needs_gold_review",
  "challenge_status": "passed",
  "memory_status": "passed",
  "reproduction_status": "passed",
  "stage_b_tooling_status": "passed",
  "stage_b_data_status": "awaiting_real_corpus",
  "stage_b_annotation_status": "awaiting_human_labels",
  "all_required_a_checks_passed": false,
  "ready_for_real_corpus_pilot": true,
  "ready_for_scientific_claims": false
}
```

示例只说明状态可以不同，禁止原样写成实测结果。ready_for_real_corpus_pilot 表示工具、版本与失败透明度足以开始独立诊断，不代表旧语料全部通过。无全局歧义 all_passed 掩盖未完成人工环节。

验收器反例至少覆盖：正确行数但错误 selected ID、修改汇总、漏行/重复、全返回空、旧主题退化、新正例掩盖退化、假 calibration、memory 未接线、qrels 缺失/未标注、模块从原工作区导入。

## 9. B-Prep：完成工具，真实输入缺失不能造假

本轮完成可运行代码与模板，不要求凭空完成真实语料或人工盲评。若仓库已有可核验真实数据，使用其副本执行导入校验；不得把 synthetic 改成 false。

### 9.1 真实文献导入

支持 JSONL/CSV 本地导入，字段：

```text
paper_id, title, abstract, year, source_url, source_provider
doi, arxiv_id, retrieved_at, version, field, synthetic
```

title/abstract/year/source_url/synthetic 为必需。synthetic 必须为 false；缺摘要列入排除表，不生成或补写摘要。来源 URL 只作溯源，离线时标记未在线验证，不能仅因为有 URL 就写 verified。

DOI/arXiv ID 规范化、版本合并、重复正文检查；保留独立 family 映射及人工修正入口。年份依旧 year<cutoff。未知 field 保留 unknown，关闭该条 field context，不根据结果反推学科。

真实检索基线先不使用自动推测 recognition：统一 retrieval policy 为 balanced_off，policy/memory/field 重排全部关闭，纯比较 evidence 检索。下一阶段再冻结真实 recognition 快照进行机制实验。

### 9.2 查询与标注工具

目标规模写入计划：3 主题、100–200 篇去重真实文献、20–30 条人工查询。主题为 agent_memory、science_retrieval、science_evaluation。

queries.csv 包括 query_id、topic_id、query_text、intent、author_type、split。人工查询应来自真实需求，不由 positive_phrases 批量生成。没有人工输入时只输出空模板和格式示例，示例不得混入正式数据。

实现 free-text query 对检索评分的实际作用，不能读入 query_text 后忽略它、仅按 topic_id 重复检索。同主题不同查询有区分测试。

支持三个冻结检索器：透明词法基线（例如本地 BM25）、Closure guarded、repaired。显式记录旧 topic-only 模式的局限。BM25 可标准库实现，参数固定并用手算小例子校验；不额外部署服务。

合并各检索器前10候选，去重后生成盲标注包。隐藏 ranker、policy、得分和原 rank；保留查询、文献原文与来源便于判断。私有映射单独保存，不放入给标注者的文件。

每条人工评分使用 0=无关、1=部分相关、2=直接相关，允许 unsure，记录 reviewer_id、时间和依据。至少两名独立标注者；Codex 不代填人类评分或签名。

两人完成后计算原始一致率和线性加权 Cohen kappa；缺标、单一类别导致未定义等情况显式报告。裁决单列，不能用裁决后的标签计算独立评分一致性。

按 family 隔离校准和留出文献，近重复不跨集合；同时划分查询，保留 query-family 交叉情况。gold incomplete 时报告 judged coverage；未判定不是负例。候选池标注产生的 recall 只能叫 pooled recall，不能称完整语料 recall。

### 9.3 下一阶段入口

实现并测试以下命令；缺数据或标注时产生可读状态文件，停止相应分析而不是伪造成功：

```bash
python3 execute_stage_b_retrieval.py init --output outputs_stage_b/pilot_001
python3 execute_stage_b_retrieval.py import-corpus --input real_papers.jsonl --run-dir outputs_stage_b/pilot_001
python3 execute_stage_b_retrieval.py import-queries --input queries.csv --run-dir outputs_stage_b/pilot_001
python3 execute_stage_b_retrieval.py pool --run-dir outputs_stage_b/pilot_001
python3 execute_stage_b_retrieval.py export-annotation --run-dir outputs_stage_b/pilot_001
python3 execute_stage_b_retrieval.py import-annotations --input reviewer_scores.csv --run-dir outputs_stage_b/pilot_001
python3 execute_stage_b_retrieval.py analyze --run-dir outputs_stage_b/pilot_001
```

人工标注尚未回来时，B-Prep 的工具验收可以完成，B 真实实验状态必须 pending。无需停下向用户询问后才编写工具。

## 10. 一键执行、环境与恢复

本轮要实现的新入口：

```bash
python3 execute_stage_a_repair.py all --config configs/stage_a_repair.json --output outputs_stage_a_repair/repair_001
```

子命令至少支持 check、audit-gold、calibrate、freeze、run、analyze、validate、reproduce、package。所有路径相对项目 root 解析，支持 --dry-run 输出实际 case 计划。

all 顺序：环境/工作区 → 数据集拆分与历史复算 → gold 审计提案 → 真实 calibration → freeze → E1–E4 → 指标复算/验收 → B-Prep 工具与模板测试 → 独立目录复现 → 中文报告 → 归档。

AGENTS.md 必需命令：

```bash
python3 --version
python3 run.py doctor --config configs/mock.json
python3 run.py test
python3 run.py run --config configs/mock.json
```

使用现有 Linux 环境；如需新环境，用 python3 -m venv .venv 后激活，按仓库 requirements.txt 安装。将依赖安装请求与实验期间网络请求分开记录，不把 pip 安装流量隐去或算成模型调用。

恢复契约覆盖实际传递依赖：topics、policy、backend、engine、state、retrieval、runner、评估器和输入；不要只 hash 三个文件。执行缓存与评估缓存分开：gold 改动只能使评估缓存失效，不能改变生产排序。

case 完成原子写入；半截 JSONL、重复 key 或缓存不匹配须检测并安全恢复/拒绝复用。调用量分 E0/E1/E2/E3/E4、校准、复现、缓存命中，不能固定写 1944 充当真实计算数。

退出码：0=该命令目标完成且必需门槛通过；2=完成但存在质量/审阅限制；1=运行失败。all 即使最终退出2也必须先完成可执行工作并归档。

## 11. 交付与可复现包

至少输出：

```text
README_REPRODUCE.md
CODE_CHANGE_REPORT.md
FINAL_REPORT_ZH.md
CALIBRATION_RECORD_CORRECTION.md
DATASET_REGISTRY.json
FROZEN_PROTOCOL.json
DELIVERY_VALIDATION.json
runtime_manifest.json
usage.json
gold_audit.csv
qrels_revision_proposal.jsonl
calibration_trials.csv
calibration_case_results.jsonl
core_results.jsonl
metrics_by_dataset_topic.csv
source_contribution.csv
challenge_results.jsonl
memory_effects.jsonl
policy_effects.jsonl
failure_cases.jsonl
engine_validation/
repository_regression/
stage_b_prep/
reproduction/source/
reproduction/REPRODUCTION_RESULT.json
CHECKSUMS.sha256
```

保留原包目录结构、必要 source/config/data/tests。独立临时目录真正解压后执行新入口，禁用原工作区 PYTHONPATH；实际模块必须来自解压目录。输入版本与核心结果语义一致、浮点容差预先冻结为1e-12；时间戳不要求相同。

归档不要嵌套旧 ZIP 或完整历史 outputs；只保存复现所需数据与基线实现。必要历史参照做有哈希的小型摘录并注明来源。外层 ZIP 在完成文件后生成，校验清单不包含自身和 ZIP 自身。

中文报告必须分开给出：旧语料两种旧算法与修复算法的结果、旧/提案 gold 双口径、挑战集类别结果、memory 响应、真实 calibration、工程回归和待人工输入。

明确回答：旧退化是否减少？哪些仍属于正文不足或标签争议？新增短语样本是否仍主导结果？memory 是否实际生效？B 工具是否可用？真实数据/标注是否已经存在？

## 12. 停止条件

本轮最多一次规则修正与最多6组参数的真实 calibration；测试后不继续自动扩大网格或生成正例。保留未通过项并交付。

完成 A 的有限修正、透明失败记录和 B 的准备工具后，下一步应由真实文献及独立标注提供新证据。不要再以合成语料全满分作为进入真实数据诊断的唯一条件。

工程完成、旧回归通过、标注可信、真实检索有效和科研社会机制有效，是五个不同结论，报告不得合并。

## 13. 给 VS Code Codex 的简短启动语

请完整读取 SCIMIRROR_STAGE_A_REPAIR_AND_STAGE_B_PREP_CODEX_TASK.md 和 AGENTS.md，直接完成 A-Repair 的代码修改、Linux 离线实验、分层验收及归档，并完成 B-Prep 的真实文献导入与盲标注工具。保留工作区与历史数据。校准必须实际运行；旧语料和新增挑战集分开验收；不得代填人工标注。缺少真实数据时完成工具与空模板并记录 pending，不要停止在计划阶段。
