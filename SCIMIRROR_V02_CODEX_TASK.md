# SciMirror v0.2：四项机制升级执行文档

交付对象：VS Code 中的 Codex。用户已授权按本文修改项目、编写测试和运行本地 mock 实验。本文是执行规格，不是已实现功能声明。

## 0. 任务与执行边界

在用户当前 SciMirror 项目上完成以下四项升级：

1. recognition 与 novelty 在计算定义上解耦。
2. policy 经可观察的决策机制影响选题、检索、合作邀请和项目退出。
3. closed/open 对同一邀请机会提供相同数量的候选人，主要改变学科组成。
4. 将 outputs 拆为草案、项目、团队最终产出和贡献/投入归一化产出。

请完成实现、测试、实验及报告，不只回复设计建议。先检查当前仓库，保留用户已有的真实语料、模型适配、缓存、实验结果和未提交修改。文中的文件名以v0.1为基准；若用户已升级，映射到等价模块，不覆盖已有实现。遵守仓库AGENTS.md。缺少真实语料或API凭据不阻塞本任务的mock实现与验证。

默认不下载文献、不安装模型、不调用收费API、不发布代码。本轮不要求新增Responses API或完整人工评审服务。保留现有后端并更新输出校验；真实LLM是否验证必须单独报告。不得请求用户把密钥发进聊天。

核心验收是机制可执行、可关闭、可审计；不是让novelty组必然优于其他组。不得调参直到效应显著，不得将policy名称直接映射为最终评价分数。

## 1. 开始前的检查与兼容策略

读取 README.md、docs/EXPERIMENT.md、配置及以下模块：state、engine、corpus、backend、events、metrics、experiment、run.py、execute.py、tests。

先运行旧测试，记录Python版本、测试结果和已有工作树状态。创建UPGRADE_PLAN.md，列出当前实现与本文的对应位置，随后执行，不需要等待再次确认。

已知v0.1行为，务必在当前代码核对：
- recognition=1-novelty，balanced奖励恒为0.5。
- 检索仅使用agent.field，候选生成后才受政策效用影响。
- 邀请带有固定跨领域加分，closed/open候选池大小不等。
- 没有实际项目退出，成员草案被吸收导致outputs与合作数量机械耦合。
- 六阶段、20Agent、单项目容量、团队至多2人，日志靠完整快照重放。

v0.2使用新的schema/config/cache版本和输出目录。旧输出只读。旧配置通过显式版本适配运行，或以清楚的信息拒绝；不能将旧配置静默解释为新机制。不要求跨版本逐位复现，但同一v0.2配置与seed的mock运行必须可重复。旧重放能力保留。

## 2. 修改一：recognition独立定义

### 2.1 明确区分三种分数

| 名称 | 产生时机 | 用途 |
|---|---|---|
| expected_novelty/recognition/feasibility | 决策前，基于可见证据估计 | Agent主观行动效用 |
| institutional_novelty/recognition | 成果提交后，固定规则计算 | 模拟制度奖励 |
| external_review_scores | 实验结束后、盲法评价 | 科研质量检验，不能反馈给Agent |

三者不得共用字段而混淆来源。名称中的independent仅指非代数互补及评价流程隔离，不声称统计独立或已测量真实学术认可。

### 2.2 v0.2默认recognition代理：历史主题关注度

使用截止时间之前固定语料中的主题频率，定义community_attention_proxy，不再以全文相似度或1-novelty定义认可。

输入每篇论文的topic_ids（允许多标签），并单独保存固定topics.json，含topic_id、description、keywords、field。mock提供明确的合成主题元数据；真实语料无标签时，允许用固定关键词规则预处理并生成审计报告，不使用policy参与标注。无匹配归为unknown并报告覆盖率；禁止给全部论文随意填相同主题来通过校验。

设每篇有标签论文向其m个主题各贡献1/m计数，C_k为主题累计计数，N为有标签论文数，K为主题数，平滑参数alpha=1：

    p_k = (C_k + alpha) / (N + alpha*K)
    attention_k = p_k / max_j(p_j)
    recognition(z) = mean(attention_k for k in fixed_classify(z))

fixed_classify对最终标题/假设/方法用同一个冻结主题分类器识别主题，不直接信任生成器自报标签。无可识别主题时置0并标记unknown；评分用到的主题ID全部输出。max为语料预处理时固定值，禁止每个实验组重新归一化。所有组共用同一语料、主题词表、分类器与统计快照。

novelty可以暂保留词汇代理，名称明确为lexical_novelty_proxy；后续更换embedding不得成为本次升级阻塞项。

制度奖励继续为：balanced=(0.5,0.5)，novelty=(1,0)，recognition=(0,1)，但balanced不再恒定0.5。热门领域中的新方法可以同时获得较高novelty与recognition；冷门领域的重复方案可以同时较低。若在真实样本上相关，报告实际相关性，不能宣称必须相关为零。

不要用当前累计引用量模拟历史引用量。此版本不需要citation_count；将来加入时必须是截点时可得的引用快照。

### 2.3 压力反馈的必要修正

v0.1的pressure += 0.03*(0.5-reward)不得用于v0.2心理结论。默认冻结pressure并标记pressure_mode=frozen；未来若增加工作量/截止期压力模型，应单独作为实验因子。本轮不再用压力变化作为主要结果。声誉可继续因制度奖励变化，但必须命名为simulated_reputation并记录更新规则。

## 3. 修改二：policy贯穿四个决策阶段

### 3.1 统一且可消融的决策接口

实现PolicyContext和统一决策记录。每个阶段有独立开关：topic、retrieval、invitation、exit。关闭某通路时该阶段使用固定neutral/balanced政策上下文，既不能在Python权重中使用实际policy，也不能把实际policy名称泄漏到该阶段LLM提示词。

    U_i(a) = U_individual_i(a) + lambda_stage * (w_n*N_hat(a)+w_r*R_hat(a)) - cost(a)

N_hat/R_hat必须来自当时可见信息，记录估计依据，不能访问最终评审结果。默认lambda_stage=0.5，各分量先规范到[0,1]，个人项不要重复计入cost。行动以稳定softmax/成对logistic抽样；提供temperature与数值稳定处理。零概率/无合法动作回退为记录明确的wait/no_action。

只给候选行动赋予制度效用，不写“novelty政策必须跨学科”“recognition政策必须退出”。领域差异不能直接等同于新颖性。

每次decision记录：agent、tick、stage、候选ID、主观特征、个人效用、政策项、成本、总效用、概率、所选行动、policy通路开关、随机流键、引用的证据ID。私有记录仅实验者可见。

### 3.2 选题

从冻结topics构建固定数量候选主题（默认3）；同一快照下候选主题集合不由policy筛掉，policy影响选择。主题特征可由agent此前知识覆盖/主题熟悉度与历史attention确定，明确这是操作化代理。

新增selected_topic_id、topic_choice_history。先选题，后检索，再生成两个候选Idea。原有Idea选择阶段保留政策效用，单独设置开关idea_selection，便于区分选题通路与提案选择通路。

### 3.3 检索

基于selected_topic_id、个人领域和近期记忆形成query。构造统一大小的相关候选池（默认20，语料不足时共同缩减并记录），再选top_k=3。

    retrieval_score = 0.5*query_relevance + 0.5*policy_weighted_features

探索特征可定义为相对agent已读文献集合的词汇差异，认可特征为论文主题attention；空阅读历史的探索特征统一为0.5。每个特征独立保存。不得仅修改prompt而实际检索仍只用field。

各组候选池规则和阅读数量一致；干预后agent选题不同造成query不同是允许的中介过程，不强制相同检索结果。保留年份过滤与可见引用校验。

### 3.4 合作邀请与接受

候选数量由第4节控制，政策只对给定候选进行偏好选择。删除无条件的“跨领域+0.3”。

候选特征来自公开的研究主题/专业知识与自身信任：知识覆盖增益、topic匹配、公开声誉、历史合作质量、预计协调成本。知识覆盖增益不能仅是field不同的布尔值；可用公开topic集合与当前研究需求的覆盖差异。认可收益可使用候选公开topic的attention与公开模拟声誉组合，明确系数。

接受方仍然自主判断、多邀请择一，保持单项目容量和最多2人团队；接受效用同样可以使用invitation通路开关。候选ID顺序、softmax参数和tie-break不能依赖agent遍历顺序。记录邀请失败原因。

### 3.5 项目退出

新增Project实体及真实状态机，不能只增加一个无作用的exit日志。实体至少含：id、origin_draft_ids、owner、current_members、historical_contributors、status、created_tick、closed_tick、version_ids、remaining_budget、effort_ledger。

状态：active -> completed / abandoned / merged；周期末仍active记censored，不伪装为完成。成员离队记录membership事件，不自动等同项目废弃。

保留6阶段：0选题/检索/提案，1建立初始项目及邀请，2接受与项目关联，3共享后选择continue/exit，4存续项目修订，5提交及反馈。所有单人项目也经过继续/退出与修订，不给独立研究者跳过成本的特权。

接受合作时，将跟随者个人项目标记merged并链接目标项目，不能再算作独立最终成果；草案和已有投入全部保留且只入账一次。初始20个选定草案形成20个项目提案，再通过合并形成存续项目。

退出决策：

    U_continue = expected_individual_gain + lambda_exit*expected_policy_gain - remaining_cost
    U_exit = outside_option - switching_cost
    P(exit) = sigmoid((U_exit-U_continue)/temperature)

outside_option默认固定配置值，不能读未来Idea真实得分；可用历史可见机会代理。sunk cost保留账本，但不得在剩余成本中重复计算。每周期每人最多一次退出决定。

成员退出后：有剩余成员则项目继续；负责人退出则按有记录的中性规则选接任者；全部退出则abandoned。退出者本周期wait，下一周期可重新立项，避免无限退出重启刷草案。过去真实贡献不因退出抹除；退出后不能读取新的队内私有事件。并发离队先在快照上生成意图，再整体解析，不因执行顺序改变接任结果。

## 4. 修改三：匹配候选人数

### 4.1 冻结候选日程，默认K=3

20人、领域7/7/6、10位轮换负责人/10位候选合作者的原结构下，某些领域只有3位候选，因此默认K=3而不是5。初始化时为整个时间表检查可行性。

对同一(seed,cycle,leader)预生成与policy无关的候选列表：closed恰好3位同领域；open恰好3位，默认1位同领域+2位其他领域。open是此实验中明确的混合学科候选条件，不再是“任意大小的全体池”。列表不重复、不含自己；仅从本周期规定的跟随者池中取人。

在新初始化或fork时固定schedule及hash，六组共享构造源。不得根据政策导致的后续资源/可用性过滤列表后偷偷补人；保留候选槽位，实际不可接受状态在邀请或接受阶段处理，并分别记录offered_count与actionable_count。

如果领域规模不足以满足配额，所有相关配对条件使用共同可行K并记录降级；默认标准20人配置应严格K=3。禁止重复填人或为closed补入异领域候选。无法形成open跨域配额时明确将该设计标为不可用，不静默称为open。

### 4.2 约束进一步混杂

候选构造不使用policy、最终成绩或私有偏好。利用公开基线声誉、能力和历史合作程度做尽可能的匹配，并输出候选特征均值差。mock初始化保证领域不与能力/偏好机械绑定。

相同候选数不能保证只剩学科组成差异：候选身份、接收邀请负荷和关系结构仍可能变化。分别报告invitation_load分布、候选属性平衡、冲突率及缺额率。不要声称完成K匹配就自动识别纯跨学科因果效应。

验收要求数量精确匹配；其他特征无法精确匹配时如实报告，而非选择性删掉不平衡样本。

## 5. 修改四：输出与投入计量

为候选Idea、选定草案、项目、版本和最终成果分配不同稳定ID。ID不能把policy名称嵌进评价文本。退出、合并、版本修订不得删除旧对象或重新计数。

| 指标 | 定义 |
|---|---|
| candidate_ideas_generated | 校验成功的初始候选Idea数量；不是LLM重试次数 |
| drafts_created | 每次决策选定并持久化的草案数量；修订不新增草案 |
| projects_started | 新建项目ID数，含以后merged/abandoned者 |
| projects_merged | 被吸收并链接到存续项目的数量 |
| projects_abandoned | 全体退出等明确终止的项目数 |
| projects_completed | 满足提交条件且只计一次的项目数 |
| projects_censored | 观察截止尚未终结的项目数 |
| final_outputs | completed项目对应的去重最终Idea卡数 |
| solo_outputs | 仅1名有正贡献的历史贡献者的最终成果 |
| team_outputs | 至少2名有正贡献的历史贡献者的最终成果 |
| total_effort_units | 干预期所有研究投入，包含失败、被合并、退出项目 |
| outputs_per_100_effort | 100*final_outputs/total_effort_units |
| mean_contributors_per_output | 最终成果的实际贡献者数量均值 |
| mean_active_team_size_at_completion | 完成时在队人数，独立于历史贡献人数 |

对每个完成成果p，成员i的份额：

    credit_i,p = effort_i,p / sum_j(effort_j,p)

每个成果份额之和=1；每个Agent累计fractional_output_credit，社会总credit=final_outputs。这个总credit是分配守恒，不能当作新的生产率提升指标。

额外输出team_size_discounted_outputs = sum_p(1/number_of_contributors_p)，明确其为按人头折算的代理，会结构性惩罚大团队，不能作为唯一优劣标准。主资源归一化指标使用outputs_per_100_effort。

effort单位用固定“模拟时间单位”，保持跨组相同行动成本配置；LLM token与HTTP次数另记，不能混入研究劳动单位。为检索、提案、沟通、修订、退出切换分别记账；零投入的完成项目应视为错误，整体无投入时比率为null并记录原因。

公共准备投入如何归属必须固定：提案成本归个人初始项目，合并只转移引用不复制成本；共享/修订记到目标项目，失败和退出投入留在总分母。共同前缀投入不进入干预期分母，跨前缀项目须分开记录period_cost。本版只在周期边界分叉以减少歧义。

输出项目流量守恒与贡献守恒验收，不再宣称final_outputs等于初始草案数减去合作数；退出与合并均有独立作用。所有计数标注累计/当期和观察区间。

## 6. 代码组织建议

按现有仓库映射实现，避免重复架构：

| 模块 | 职责 |
|---|---|
| recognition.py / topics.py | 冻结主题统计、分类器、attention评分 |
| policy.py | PolicyContext、通路开关、效用分解 |
| candidate_pool.py | 定长匹配候选日程及审计 |
| projects.py | 项目状态机、合并/退出/接任 |
| accounting.py | 不重复投入账本、份额和计数 |
| state.py | 新增Agent公开主题、Project、状态校验 |
| engine.py | 保留六阶段，接入四条机制 |
| backend.py | 增加topic字段并更新mock与LLM schema校验 |
| metrics.py / experiment.py | 新指标、行为轨迹、对照及归档 |
| tests/ | 行为单测、配对测试、兼容与集成测试 |

新增模块可合并但职责必须清楚。词汇实现与bootstrap可继续用标准库；若引入依赖，固定版本并说明用途。

当前mock应改成响应topic/检索证据/共享内容的确定性生成器：不同topic改变文本，检索论文改变支持证据，合作内容能被修订使用。不可直接根据policy返回高分模板。mock的目的仍是机制与协议测试，不用于证明科学创新。

## 7. 配置与消融

在现有完整配置上新增以下片段（不是完整可执行配置）：

```json
{
  "schema_version": "0.2",
  "recognition": {"method": "community_attention", "alpha": 1.0, "freeze_at_fork": true},
  "policy_paths": {"topic": true, "retrieval": true, "idea_selection": true, "invitation": true, "exit": true},
  "candidate_pool": {"size": 3, "open_same_field": 1, "open_other_field": 2},
  "topic_candidates": 3,
  "retrieval_candidate_pool": 20,
  "retrieval_top_k": 3,
  "project_exit": {"enabled": true, "max_per_agent_per_cycle": 1},
  "pressure_mode": "frozen",
  "dynamic_values": false
}
```

创建完整v02_mock_smoke.json、v02_mock_full.json、v02_llm_pilot.json，以及消融配置或自动配置生成器。

必跑mock：20Agent、30tick、branch_tick=6、3seed、3policy×2network。所有组共同前缀必须使用相同机制开关；消融在fork后生效，不能各自运行不同前缀。

消融：full、no_topic、no_retrieval、no_invitation、no_exit_policy，以及all_policy_paths_off。no_exit_policy只关闭退出效用中的政策项，不能禁用退出动作；动作空间必须相同。all_policy_paths_off同时关闭idea_selection及所有其他政策路径，并将制度反馈也固定到balanced，确保真正没有剩余政策泄漏。

先3seed smoke，再对full用10个预先固定的新seed做本地mock诊断；资源不足可报告实际完成数。不要为得到显著结果更换seed。LLM配置只生成并估算，不默认执行收费请求。

## 8. 测试和验收（必须新增）

1. recognition解耦：改变冻结topic频率而保持Idea和novelty scorer不变，recognition改变而novelty不变；构造至少两种不同balanced奖励。不要测试相关系数必须为零。
2. 截止期：所有主题统计和检索只用截点前数据；未来文献插入不能改变分数、候选主题或检索。
3. 四通路：相同状态与随机流，分别改变policy，检查对应阶段的效用/概率确实改变；不要求有限样本下每次所选动作都不同。
4. all_policy_paths_off：相同seed与network的三policy运行在剔除纯标签后状态与动作一致；包括prompt、缓存键、奖励反馈的泄漏检查。
5. 候选匹配：标准配置所有配对邀请机会closed/open均3个候选；配额正确、无重复、无自身；不足时共同降级或明确失败。
6. 网络属性：候选表不因policy改变；offered_count与actionable_count分开；输出基线属性和邀请负荷差异。
7. 退出：全部退出导致abandoned；负责人退出可接任；退出后不能获得新队内信息；沉没投入仍入账；不能周期内无限重启。
8. 计量：revision不新增draft；合并不复制成本；最终输出不重复；solo+team=final；各成果credit之和1；社会credit之和final；废弃投入进入总分母；零分母返回null。
9. 项目状态：所有start项目按终态/活跃态可核对；成员、负责人、资源、预算和版本关系有效。
10. 并发一致性：多个邀请、多人成员退出不受遍历顺序影响；模型失败不提交半个tick。
11. 重放：每个最终状态20Agent、tick=30；同seed所有分支源哈希相同；日志重放与最终状态一致。
12. 集成：18行主汇总、72行周期轨迹；真实发生的退出/合并等由对应事件支持。无退出的seed可合法，但用定向fixture覆盖可退出路径。
13. HTTP/schema：保留兼容后端本地测试；新增字段校验、缓存版本隔离，未提供真实凭据时不假装真实端点通过。

所有关键不变量用运行时校验，不只依赖可以被python -O移除的assert。测试可以用assert方法。

## 9. 实验输出与分析

每次运行保留config.json、manifest.json（源码/配置/语料/topic/候选日程hash）、status.json、usage.json、每分支final_state.json、完整事件日志、checkpoint、独立replay_validation.json。

新增candidate_pool_audit.csv、decision_audit.jsonl、project_ledger.csv、effort_ledger.csv、agent_credits.csv、schema说明。盲评导出不包含policy、network、奖励、主观分数或可解码条件标签。

主表包含第5节指标及选题分布、阅读主题分布、邀请接受/冲突率、退出率、跨领域产出比例。明确cross_field_rate以历史正贡献者定义，完成时当前成员口径另列。

对统计比较：
- 同network下novelty/recognition分别相对balanced，世界seed内配对。
- 同policy下open相对closed，世界seed内配对。
- 输出政策×网络交互差，例如(novelty_open-balanced_open)-(novelty_closed-balanced_closed)。
- 在世界层bootstrap，不把Agent/Idea当独立重复。3seed区间仅探索，不能用未跨零作为通过标准。
- 消融比较只能支持模拟机制归因检查，不直接证明现实中介因果关系。

撰写UPGRADE_REPORT.md：旧问题、实际实现、文件映射、测试结果、四项验收、剩余混杂、运行模式、调用成本、输出位置、是否真实LLM验证。将干预有效性与最终质量分开；让policy进入行为效用本身是建模假设，其产生变化不能被称为已发现现实规律。

## 10. 执行顺序及交付完成标准

1. 检查仓库和旧测试，记录计划与兼容策略。
2. 先做recognition和Project/账本对象，建立不变量测试。
3. 做候选日程与匹配审计，再接入四通路。
4. 更新mock、配置、日志和独立指标；跑定向测试。
5. 跑18分支完整mock及消融，自动验收分叉和重放。
6. 修复实际错误；不要以增加显著性为理由修改参数。
7. 更新README和实验协议，生成报告及验收JSON。

提供新的跨平台入口execute_v02.py，依次环境检查、测试、v02 mock实验、归档验收；不覆盖execute.py旧入口。提供dry-run/estimate，只估算LLM调用，不发请求。

最终向用户报告：修改了什么、四项是否分别验收、通过/失败测试数、20Agent和各条件实际运行数、归档目录、未解决问题。缺真实数据、实机Windows或真实模型验证必须明确写出。交付可运行v0.2，不声称仅完成这些工程升级就已经达到真实科研社会有效性。

## 11. 用户给Codex的启动指令

请读取当前项目中的SCIMIRROR_V02_CODEX_TASK.md，按文档在现有代码上完成四项机制升级。先核对已有修改和版本，不覆盖旧结果；完成实现、测试、20Agent的mock主实验及消融，并生成执行报告与重放验收。默认不调用收费API。请持续执行到文档中的交付标准完成，只有遇到真实权限、环境或不可消除的需求冲突时再报告具体阻塞。
