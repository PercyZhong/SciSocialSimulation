# SciMirror v0.3：检索有效性、独立盲评与质量计量升级执行文档

交付对象：VS Code 中的 Codex。日期：2026-09-10。

本文是对现有 SciMirror v0.2 的执行规格，不是已实现功能声明。请在实际仓库中完成代码、配置、测试、实验和报告。用户已授权本次三项升级；不要只回复方案。现有代码优先，文中模块名允许映射到等价职责。

## 0. 目标、已知证据与执行边界

完成三个目标：

1. 定位并修复或解释选题—检索弱联系，记录文献重合、主题匹配、相关性和近重复诊断。
2. 建立可真正使用的独立盲评流程，完成分层抽样、身份隔离、评分导入、覆盖与一致性分析；有已配置且获准使用的真实评审资源时完成真实评分。
3. 将数量解释拆为合并、成员退出/项目废弃、完成和质量，增加抽样适用的质量调整产出、重复成果与单人/团队比较。

已知 v0.2 证据：mock、90篇合成文献、HTTP=0。固定 policy/Agent/tick 后，跨3 seed×2网络的 query 虽变化，但 selected_ids 完全一致；关闭检索政策路径后文本指标效应大幅衰减。main 941条、diagnostic 3057条盲评材料评分全部为空。数量增加部分来自较少项目合并与更多单人完成。这些是待解释线索，不预先认定某行代码有错。

执行限制：

- 保留用户未提交修改、旧入口、v0.2输出、已有真实语料/模型配置；旧结果只读。
- 使用新 schema、缓存命名空间和 outputs_v03；对旧数据做显式适配，不重新解释旧指标。
- 默认不调用收费API，不下载大模型，不要求GPU；缺真实模型或人工评分不阻塞本地实现、抽样和验收。
- 不通过调参到显著、选择seed、强制换文献或按policy模板打分来通过验收。
- 当前任务授权修改代码与本地实验，不自动授权新增付费评分。已有会话明确预算授权则沿用；仅有API密钥不等于预算授权。
- 不向聊天、日志、文档输出API密钥。不发布代码，不给他人发送评分邀请。
- 无真实评分资源时交付完整可运行流程和待评包，状态明确为 awaiting_external_reviews；不能假称已完成独立盲评。

## 1. 先核对仓库并建立基线

读取 AGENTS.md、README、旧执行规格、配置、state/engine/corpus/backend/events/metrics/experiment及项目/账本模块。检查现有改动，记录当前commit、源码hash、Python和依赖锁文件。不要直接覆盖与文档同名的已有实现。

生成 V03_UPGRADE_PLAN.md：实际文件映射、发现的问题、兼容策略、预期更改、可用评审资源。随后自主执行。

运行原有必要测试；保留原有失败与本次新增失败的区别。若上传结果可用，读取 summary、decision_audit、project/effort/credit账本及config，复算关键基线。缺旧归档则先运行一个冻结v0.2基线并记录来源，不把重跑冒充历史数据。

旧版输出只读。保留 legacy_v02 检索模式用于受控比较，但不要复制整个旧架构。

## 2. 代码职责与数据协议

建议在现有包下映射以下职责：

| 模块 | 职责 |
|---|---|
| retrieval/query.py | topic ID→描述/关键词，查询构建与规范化 |
| retrieval/ranker.py | 相关性召回、相关性门槛、政策重排序、文献去重 |
| retrieval/audit.py | 查询反事实探针、实际轨迹诊断与阶段日志 |
| review/sampling.py | 冻结总体、分层抽样、纳入概率 |
| review/blinding.py | 随机review ID、公开包和私有映射隔离 |
| review/rubric.py、adapters.py | 评分规范、人工导入、真实模型适配、mock测试 |
| review/validation.py、agreement.py | 评分完整性、来源验证、一致性与缺失分析 |
| metrics/quality.py、duplicates.py | 质量合成、抽样加权、成果近重复 |
| analysis/decomposition.py | 项目流量、成员退出、投入和质量分解 |
| execute_v03.py | 检查、测试、实验、导出、导入、分析和归档 |

不能为了符合表格另建重复引擎。所有新配置与数据有 schema_version；随机抽样种子与世界模拟随机流分离。

检索事件引用 query_id、agent_id、tick、branch、selected_topic、corpus_hash、query_builder_version、候选/所选文献、分数分量、fallback。复用现有事件封装和哈希协议；不要把完整文献正文重复写入每条事件。

评审和实验后分析位于模拟引擎外。外评器不读Agent私有状态、policy效用、原制度分数或条件映射。评审不得更新Agent、改变已完成分支或进入模拟决策缓存。

## 3. 修改一：让选题与检索的关系可检查

### 3.1 先定位问题，不凭最终指标猜测

在相同语料、Agent知识状态和policy下执行冻结状态查询探针：

1. 保持主题不变，使用等义查询表达，观察结果稳定性。
2. 只改变 selected_topic，保持领域、记忆、阅读历史、policy、候选预算不变。
3. 只改变policy，保持query与基础候选池不变。
4. 同输入重复运行，核对确定性、tie-break和缓存。

分别检查 topic ID 下划线分词、查询字段是否实际进入召回、top-k截断、空/零相关结果、记忆长度、固定field过滤、主题词与语料用词不一致、分数尺度、缓存键遗漏和近重复挤占。

报告现象是否能复现、在哪一层出现。自然运行跨seed结果相同不是错误的充分条件；禁止要求每次换题都必须换文献。

### 3.2 查询和排序实现

- 查询保留 topic_id，同时将其解析为冻结 topics.json 中 description/keywords。明确配置topic、field、memory权重；保存各字段解析结果。
- 基础召回使用真实文本相关性，默认CPU可用的确定性词法方案（优先复用现有BM25/TF-IDF）；清楚记录分词与版本。
- 固定语料年份截点，各条件相同。不得通过新增未来文献改变检索或attention。
- 相关性池默认20、最终top_k=3。当前领域可以是加权特征；若原先是硬过滤，是否修改必须成为显式配置和独立比较项，不能悄悄扩大检索范围。
- 同一冻结query下，policy只能重排序共用基础候选池，不能偷偷改变召回预算。
- 先定义相关性合格集合，再在其中重排序。词法分数全为0时不能简单归一化成高相关。门槛由冻结合成fixture或独立校准集设定，不能按六条件实验效果选择。
- 输出原始相关性、归一化相关性、探索分数、attention、政策分数、最终分数。参数固定后，各条件共用。
- 合格候选少于3时返回实际数量，记录shortfall/fallback；不得用无关文献或重复文献补满。无证据时采用明确的低证据/等待协议，并记录对产出的影响。
- 文献先按ID/DOI等确切标识去重；近重复用冻结的文本规则标注和可配置限额。不能把同topic的不同研究都当重复。
- 近重复簇内优先保留高相关且具有稳定tie-break的文献；不为了多样性强制插入低相关或跨领域文献。
- 缓存键至少包含语料、查询内容与构建器、排序配置、去重版本；Agent阅读历史或policy影响排序时必须进入相应阶段缓存键。

### 3.3 必须输出的诊断

| 指标 | 定义与边界 |
|---|---|
| document_overlap | 配对文献集合交集/并集（Jaccard）；另报交集/min集合大小；空集合返回null及原因 |
| topic_match_rate | 所选文献中包含selected_topic的比例；无标签单列，不能静默忽略 |
| topic_coverage | 所选文献覆盖的相关主题集合；与匹配率分开 |
| relevance_distribution | 基础池和所选集的原始相关性分位数、零分比例、合格比例 |
| near_duplicate_pair_rate | 所选文献对中被冻结规则判为近重复的比例；少于2篇记null |
| unique_cluster_ratio | 所选文献近重复簇数/所选文献数 |
| retrieval_shortfall | 实际数量不足top_k的频率和原因 |
| same_query_policy_overlap | 固定query改变policy的所选集合重合率 |
| changed_topic_overlap | 冻结状态仅换topic后的文献重合率及匹配变化 |

分别输出 frozen_query_probes 与 live_retrieval_audit，禁止把跨tick的知识更新等变化当成“只换topic”的因果比较。相关性分数高只是排序器内部诊断，不是科学证据相关性的独立证明。

### 3.4 验收

- 设计词汇可区分的A/B主题fixture：只换主题应优先选中相应相关文献；等义表达保持合理稳定。
- 全零相关、少文献、未知主题、未来文献、近重复、缓存失效均有定向测试。
- 给出legacy_v02与修复模式在同一冻结状态探针上的比较，解释真实运行中仍不变的情况。
- 不以“novelty均值更高”或“效应显著”作为检索修复通过标准。

## 4. 修改二：可执行的独立盲评

### 4.1 冻结总体和分层抽样

主评审总体：选定一次运行中的干预期去重最终完成项目。不要混合smoke、消融、主实验和诊断为一个总体。冻结 population_manifest（项目ID、版本、文本/证据hash、所属世界和条件、历史正贡献者口径）。

默认以 seed×policy×network×output_type(solo/team) 分层，兼顾世界配对和团队比较。每层简单随机不放回抽min(3,N_h)项；纳入概率π_p=n_h/N_h。空层显式记录；不得把空层名额悄悄转给其他层。

10 seed、6条件、2类型的默认上限是360项、每项2位评分者共720份评分；这只是规模估计，不默认触发付费。main上限108项。用户已有预算配置优先；允许预先降低配额，必须在抽样前冻结。

保存 sample_seed、N_h、n_h、π_p、sampling_algorithm_version及样本hash。后续增加样本须使用有记录的抽样轮次和可计算纳入概率，不能只追加好看成果。近重复项目不从总体静默删除，以便估计重复率；重复聚类另行报告。

### 4.2 盲化与材料

公开包只含随机review_id、标题、假设、方法、引用清单和固定版本证据摘要/可用正文。保留评价证据支持所需信息，但移除policy/network/seed、Agent姓名、团队标识、分支路径、制度评分、文件名或ID中的条件编码。

review_id使用一次生成并持久化的随机映射，不从项目ID生成易反查短hash。私有映射与抽样权重置于private目录；公开包生成独立文件并做敏感键与条件标识扫描。审稿器文件读取范围只允许公开包和rubric。

清理文本中的条件标签必须保留编辑审计；不能删除科学内容来制造盲化。内容可能暴露研究方向或生成风格，报告盲化的实际限制，不承诺完全不可推断。

同一评分者随机呈现顺序；不同评分者不能看到彼此分数、讨论或裁决结果。先冻结所有独立评分，再做分歧处理。

### 4.3 四维评分与证据可用性

四维均为1–5，附每个维度短理由和可定位证据ID/摘录位置：

| 维度 | 1分 | 3分 | 5分 |
|---|---|---|---|
| 新颖性 | 主要重复给定参考工作 | 有明确增量，区别可描述 | 相对所给证据有清晰且重要的新思路 |
| 可行性 | 方法矛盾或不可实施 | 路径基本可行，但关键细节待补 | 方法、资源与验证计划清晰且相互一致 |
| 科学价值 | 问题或贡献不明确 | 对明确问题有潜在增量价值 | 有充分理由认为能回答重要问题或产生有用知识 |
| 证据支持 | 已核对证据与关键论断不符/支持极弱 | 部分关键论断有支持，存在缺口 | 关键背景与方法依据均有明确可定位支持 |

2/4分表示相邻锚点之间。新颖性限于可见证据，不写成全科学文献范围的新颖性。

将证据不足区分为：材料确实没有提供支持（可以低分并解释）与评审者无法访问必要材料（unassessable，分数null）。后者不能置0，也不能强迫给1分。保存 evidence_access_status、references_valid、不可评价原因和rubric_version。

作者提出的新假设不要求已经被文献证实；证据支持评价背景、已有事实和方法依据，不惩罚所有尚未验证的新假设。

### 4.4 评分后端与隔离

- human_import：生成UTF-8 CSV/JSONL模板并验证导入；不自动向他人发送材料。
- llm_external：复用仓库现有适配器，仅输入公开材料与rubric；默认配置2个评分者，记录provider/model/version/prompt_hash/参数/时间/请求与token使用/缓存/失败。
- mock_review：仅用于schema、数据流与公式测试，所有输出必须is_mock=true、validation_only=true，并禁止进入正式quality_results。
- 不把空模板当完成；不把mock评分称为独立盲评。
- 同一个模型两次采样只能称为重复LLM评分，不能称为两个独立模型。不同模型也可能共享偏差。评分者来源必须在报告中准确描述。
- 优先两位独立人工评分者，或不同模型后端加人工校准。与生成模型分离不等于判断客观；保存gen/reviewer来源关联。
- 评分缓存键覆盖review文本、证据hash、rubric、评审模型和prompt版本。失败不得悄悄换模型；重试和缺失均可追溯。
- 预算dry-run估计样本数×评分者数×每次token及重试上限；无已核准价格配置时不捏造金额。超预算停在可恢复状态。

建议评分记录：review_id、reviewer_id、reviewer_kind、model_version、四维score/reason/evidence、assessability、rubric_version、response_hash、is_mock、started_at/completed_at/status。私有条件映射不放入请求或响应schema。

### 4.5 一致性、缺失与分歧

逐维输出共同已评分项数、完全一致率、相差不超过1分比例、平均绝对分歧；两位评分者计算二次加权Cohen kappa，必要时多评分者计算序数Krippendorff alpha。使用经过验证的实现或以已知手算fixture测试，不用相关系数替代一致性。

评分恒定、样本不足或无共同项目时记null及原因。单评分者不能给出评分者间一致性。区间如计算，按世界聚类抽样，不能把所有成果视为独立世界。

预先规则：任一维相差≥2分进入分歧队列。保留原分数，一致性基于裁决前评分；第三方裁决单列，禁止通过删低分/争议项提高一致性。默认质量分析用两位原始评分者均值，裁决后结果作为单独敏感性分析。

逐条件/世界/类型报告邀请评分数、有效评分数、双评分完成数、缺失率及不可评价原因。完整评分前不得输出伪完整的质量结论。

## 5. 修改三：数量、质量与投入的联合计量

### 5.1 保留账本并拆分流量

继续校验 started=merged+abandoned+completed+censored，solo+team=final，成果贡献份额和为1。起始活跃项目若存在，使用期初存量+流入−流出=期末存量，不能套用无跨期存量的简式。

分别报告：合并项目数/率、成员离队人数与membership事件数、发生离队的项目数、全员退出导致的废弃、其他原因废弃、完成、censored、失败投入。成员退出不能与废弃项目混为一谈。

跨条件分解：Δcompleted=Δstarted−Δmerged−Δabandoned−Δcensored（仅在同一完整项目队列口径成立）。这是会计恒等分解，不是统计中介因果贡献。

### 5.2 质量合成

先报告四个维度分数，复合分数只是透明的辅助指标。默认预先固定：

    dimension_mean[p,d] = 两位原始有效评分者在该维的均值
    Q[p] = mean_d((dimension_mean[p,d] - 1) / 4)

Q∈[0,1]，四维等权。必须四维均有足够有效评分才计算Q；缺失不能置0或只对可用维度重新归一化。映射与等权是分析约定，不代表真实科研价值的比率尺度；保存quality_formula_version，并做预先指定的权重敏感性分析。

### 5.3 全量与抽样质量估计

全量评审完成时：quality_adjusted_outputs=Σ_p Q[p]；quality_per_100_effort=100×ΣQ/全部干预期投入。

分层抽样且所抽样本全部获得有效Q时：

    estimated_quality_total = Σ_h N_h × mean_{p∈sample_h} Q[p]
                            = Σ_{sample} Q[p]/π_p
    estimated_mean_quality = estimated_quality_total / population_completed_count
    estimated_quality_per_100_effort = 100×estimated_quality_total / total_effort_units

必须区分 observed_reviewed_quality_sum 与 estimated_population_quality_total；不能把只评了部分成果的ΣQ称为全体质量总产出。团队/单人使用各自抽样层与总体数。

有评分非响应或unassessable时，π只能纠正抽样，不能自动纠正评分缺失。默认将总体质量估计标为incomplete，并报告可用样本描述、缺失分布；可提供Q∈[0,1]下缺失样本上下界，不把完整案例均值冒充无偏估计。任何非响应建模必须另行说明假设。

世界层政策/网络配对仍为主；报告世界层差值和区间。抽样评分还存在世界内抽样不确定性：采用适合不放回分层设计的方差/重抽样方法并考虑有限总体修正；若只给世界bootstrap，明确其条件于当前已评分样本、未覆盖评分抽样误差。双层实现未经验证时不输出貌似精确的合并区间。

### 5.4 重复成果和团队比较

- 全部最终成果先做完全重复（规范化标题/假设/方法hash）与近重复检测，不能直接复用文献主题相同作为成果重复。
- 默认CPU词法近重复，冻结字段、规范化规则、相似度阈值和聚类算法；模型语义检测可选，不作为默认依赖。
- 每个branch内报告重复对比例、唯一簇数、簇大小分布、redundant_output_rate=(N_outputs−N_clusters)/N_outputs；N=0时比例null。跨条件重复单列，不能从某一条件扣除其他条件已有文本。
- 说明连通分量聚类的链式合并风险，报告代表性边界例子；用同义近重复和“主题相同但方法不同”fixture检验。
- 质量调整和去重复是两个指标，不能未经定义混合。抽样质量不能直接推断每个重复簇的最高质量。没有全量或专门簇抽样设计时，不输出“去重质量总和”。
- 单人/团队按历史正贡献者定义，分别报告总数、抽样/有效评分数、四维均值、加权质量均值、质量总量及重复率。完成时在队人数另列。
- 团队身份是干预后形成的变量；团队质量差异仅作描述，不宣称合作导致质量提升。将合作效应解释为因果需要额外设计。
- 如计算团队/单人投入效率，必须声明失败和合并投入的归属规则；无法完整分配时只报告社会总效率，避免给成功团队分母漏记失败成本。

## 6. 新配置和执行入口

提供完整可运行配置，不把下列约定当成现成CLI。保持v0.2的20 Agent、30 tick、branch_tick=6与3×2条件；新增键至少覆盖：

- retrieval.mode=legacy_v02/relevance_gated；查询权重、门槛、基础池大小、top_k、去重和版本。
- review.mode=human_import/llm_external/mock_review；population_run、sample_seed、strata、per_stratum=3、reviewer列表、rubric_version、预算与重试限制。
- analysis.quality_formula、缺失策略、重复规则、bootstrap种子与次数、区间类型。

实现并记录以下命令语义，支持 --help；若复用现有CLI，可提供兼容封装：

```bash
python execute_v03.py check
python execute_v03.py test
python execute_v03.py retrieval-diagnose --config configs/v03_mock_main.json
python execute_v03.py run --config configs/v03_mock_main.json
python execute_v03.py export-review --run-dir <本次运行目录> --config configs/v03_review.json
python execute_v03.py review-estimate --review-dir <评审目录>
python execute_v03.py import-reviews --review-dir <评审目录> --input <评分文件>
python execute_v03.py analyze --run-dir <本次运行目录> --review-dir <评审目录>
python execute_v03.py validate --run-dir <本次运行目录>
```

真实模型评分另设显式review-run命令；默认主入口不得暗中调用。所有阶段可恢复，已完成branch/评分按hash复用；配置冲突不得误用缓存。check/test/analyze/export-review不能调用真实模型。

## 7. 必须执行的实验与测试

顺序：

1. 原有回归与新增定向fixture。
2. 冻结状态检索诊断：legacy与修复版逐项比较。
3. 3 seed smoke；通过后跑3 seed×6条件、30 tick主实验。
4. 新版full与no_topic、no_retrieval进行最小针对性消融；其余已有消融保持可运行，不为追求显著重复扩跑。
5. 固定新seed=100–109的10 seed诊断，资源预算允许时运行；若无法完成必须报告实际数，不换seed补好结果。
6. 选择一个明确总体导出盲评。默认10 seed完成则用该总体，否则用main并明确；不把两者重复计入。
7. mock评分只跑测试集；真实评分资源可用且获准时完成评分，否则导出模板并标记等待。
8. 生成数量分析；真实有效评分未齐时，质量分析提供完整性/缺失报告，不虚构分数。

比较旧/新检索引擎时，明确是整个模拟前缀一起变化的版本对比，还是从兼容共同检查点开始的干预；不能因为seed相同就声称初态完全相同。只有序列化状态兼容且前缀hash一致时，才作共同分叉解释。

必须覆盖的测试：

- 查询topic被使用、缓存隔离、零相关回退、近重复与年份过滤。
- 抽样可重复、无重复抽取、π正确、小层全取、空层处理。
- 公开包和真实评审请求均无条件映射/制度分数；评审数据不进入引擎。
- 拒绝分数越界、重复reviewer/review_id、非法引用、错误rubric、mock混入正式结果。
- 一致性在完全一致/完全分歧/恒定分数/单评分者场景行为正确。
- 人工构造总体验证全量ΣQ、分层加权估计、全部抽中退化为全量、缺失不会被当0。
- 项目守恒、成员退出不等于废弃、历史贡献保留、失败投入入账、零分母null。
- 重复检测不会把不同方法同topic误并；跨branch重复不扣减本branch产出。
- 旧入口与旧schema显式兼容；新评审/分析不修改旧实验状态。

使用定向测试验证实际风险，不添加只复述实现的空测试。运行时关键不变量不能仅用可被python -O禁用的assert。

## 8. 输出、完成状态与最终报告

保留原有config/manifest/status/usage、summary、trajectories、paired_effects、final_state/events/checkpoints和三类账本。新增：

```text
retrieval_query_probes.jsonl
retrieval_diagnostics.csv
retrieval_before_after.md
population_manifest.json
review_public/ideas.jsonl
review_public/evidence.jsonl
review_public/rubric.md
review_public/ratings_template.csv
review_private/sample_manifest.csv
review_private/review_key.csv
review_private/reviewer_provenance.json
review_results/raw_reviews.jsonl
review_results/validated_reviews.csv
review_results/coverage.csv
review_results/agreement.csv
review_results/disagreements.csv
analysis/project_flow.csv
analysis/duplicate_outputs.csv
analysis/quality_by_world_condition.csv
analysis/quality_by_output_type.csv
analysis/quality_paired_effects.csv
V03_UPGRADE_REPORT.md
DELIVERY_VALIDATION_V03.json
```

没有真实评分的结果文件可以是有schema的空表，但status必须写明not_run/awaiting_external_reviews，不能写真实质量验收通过。

验收JSON分别记录 implementation、mock_validation、retrieval_diagnosis、simulation、review_sampling、external_review、quality_analysis；每项有status、实际检查数、失败/等待原因。overall只能在所声明的范围内completed，不允许用一个all_passed掩盖未完成外评。

报告回答：根因是否定位、如何修复、仍有何合理不变；三项分别实现到什么程度；实际实验数、seed、模型与费用；盲评是谁评、覆盖与一致性如何；数量增长由何种流量变化构成；质量是全量还是抽样估计；哪些结论尚不可解释。保留不能解释为现实因果的边界。

## 9. 用户交给Codex的启动指令

> 请读取当前项目中的 SCIMIRROR_V03_CODEX_TASK.md，在现有SciMirror v0.2上完成三项升级。先检查仓库与已有修改，保留旧结果；实现检索诊断与修复、独立盲评抽样/导入/隔离流程、项目流量与质量计量。完成定向测试、mock主实验及针对性消融、结果归档和执行报告。默认不调用收费API，不把mock评分当独立评价。缺真实评审资源时继续完成全部本地工作、导出待评包，并准确标记等待评分。不要停在计划阶段，不要为了显著性调整seed或强制产生预期结果。
