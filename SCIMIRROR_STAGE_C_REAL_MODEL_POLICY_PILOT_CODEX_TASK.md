# SciMirror Stage C：真实模型政策响应试验执行文档

日期：2026-09-27。执行对象：VS Code Codex。基于用户交付的两份20260925 Linux归档及其代码快照。先阅读本地AGENTS.md、git status与现有模块，适配较新代码，不回退用户修改。

## 1. 本轮任务与边界

完成少量Stage B收尾修复，接入两个可配置真实模型，构建冻结状态政策响应实验C0及单世界动态smoke C1。默认先完成代码、mock测试、预算估算、可执行配置和归档；当用户已提供具体模型、凭据环境变量、预算及相应live阶段授权时直接继续相应阶段，不重复确认。不得在未指定模型和预算时自行消费。独立人工核查继续延期，不阻塞本轮工程或探索性实验。

停止继续优化36文献/9查询上的检索指标。C0使用固定证据，C1采用显式新配置的BM25基线，不覆盖旧生产配置。软重排仍作为历史实验臂，不自动升级为默认检索器。

本轮不是完整科研政策因果验证，不启动不同模型混居的动态社会实验，不展开无预算的大规模模型网格。先把“模型实际决策”和“程序按政策机械决策”分离清楚。

## 2. 已核实的Stage B结论与小问题

两ZIP可解压，顶层清单96+102=198文件哈希通过。候选诊断45运行，324完整排名；对两种画像各9查询，K20与all的最终top10完全相同。独立按逐文献评分与ranked_ids复算405行×P/nDCG/Recall三个指标共1215值，最大浮点误差4.45e-16以内。

最终标签视图：BM25 nDCG@5=0.7959347506392649；原画像soft=0.7899872169073839；对齐画像soft=0.8066382959710731。所有soft臂不满足分主题不退化规则，保持candidate_for_future_validation=false。

遗漏文献openalex_W3015453090在q_science_evaluation_03的BM25排名29、分数1.03575085255；all可召回却仍不进最终top10。18个query/profile比较的归一化分母均未改变，不需要反事实分母实验。

补充敏感性：improvement结果中的严格grade=2 nDCG@5，BM25为0.8025066747272711，对齐soft为0.7928909869452447，下降约0.009616。一般相关性改善不代表最相关文献排序更好，不再据此调参。

Linux TEST_STATUS记录124测试通过，MOCK_REPLAY_VALIDATION记录18分支、20Agent、tick30和重放通过；这属于交付记录，本次分析没有重跑完整124测试和历史mock。

实际发现的可复现打包错误：

- candidate归档的reproduction/previous_run/CHECKSUMS.sha256缺失；按README执行run失败：Previous delivery checksum validation failed。
- 改用单独improvement归档作为source后，又因其reproduction/source_run/CHECKSUMS.sha256缺失而失败：Frozen pilot snapshot checksum validation failed。
- 顶层校验通过并不能证明递归依赖齐全；不能继续宣称交付ZIP已在任意独立目录端到端复现。
- candidate报告同时含“Linux需结合记录确认”和末尾“passed”，属于报告状态生成不一致。

### P0收尾修复

定位打包是否按basename全局排除了CHECKSUMS；保留所有子目录自己的清单，每份清单只排除自己，父清单可覆盖子清单。不得修改已交付ZIP；新建修复版并记录parent_archive_sha256。历史缺失清单若由当前内容补建，明确为reconstructed_manifest，不伪装成历史原始文件；在原父级清单完整性验证成功后建立来源链。

验收必须从最终生成ZIP解压到无开发仓库依赖的临时目录，实际执行README命令，记录模块加载路径及返回码，逐项比较排名和指标。不能只对打包前目录测试。状态报告由同一JSON生成，清除过时占位句。

## 3. 为什么不能只改模型名

归档backend.py仅提供propose/revise生成；v02_engine.py的topic、idea_selection、invitation、exit多处调用policy.choose，由公式与随机选择控制；retrieval query也由代码构造。只切换SCIMIRROR_MODEL，容易测成“相同规则下不同模型Idea文本差异”。

新增决策后端接口，明确decision_source：rule / llm / explicit_fallback。旧规则模式保留作工程对照，新实验为llm模式。系统负责合法动作、预算、状态迁移；模型负责从合法动作集合选择。不能LLM选完后再用policy.choose覆盖选择。

## 4. 研究问题与实验推进

RQ1：在相同状态、证据和候选集合下，同一模型在balanced/novelty/recognition下是否改变选择？
RQ2：两个模型的政策响应幅度/方向是否不同？
RQ3：候选人数相同而学科组成不同，是否改变邀请行为？

主结果是模型行为和协议可靠性，不是Idea真实科学价值。自评新颖性、自动文本距离和平台recognition只能称代理指标。

### C0：本轮主要live实验——冻结决策

默认两模型M1/M2，3个state/world seed，3种reward政策，closed/open两种合作集合，4种阶段，3次独立draw。

总逻辑决策数=2×3×3×2×4×3=432。

四种阶段：topic_selection、retrieval_query_selection、invitation、project_exit。每seed冻结一个20Agent世界，从中确定一个有合法机会的actor及对应阶段状态；该阶段在不同政策/模型下复用同一状态。输出actor_id、snapshot_id、eligibility说明；3个state seed只是小样本机制探测，不代表20Agent总体覆盖。

topic与query阶段提供政策无关的有限候选（建议各3项）；候选按稳定规则从现有主题及实际语料构造并记录provenance，不根据qrels优化。query候选各附同一BM25算法得到的最多3篇证据，候选外部使用的原查询9条标签不得泛化到新查询。

invitation阶段closed给3个同领域候选，open给1同领域+2异领域；可选择invite某ID或decline，所有臂动作语义一致。candidate人数与角色/资历等可匹配特征对齐并记录残余差异，不能声称完全隔离了真实学科因果效应。

exit阶段提供continue/exit以及固定项目进度、预期成本、外部机会。无合法退出机会不得强造；抽取合规状态并记录。

world_state_hash、nonpolicy_observation_hash、candidate_hash、evidence_hash固定可核对。政策变化只改制度说明与实际计分权重；network变化的候选组成仅在invitation阶段影响动作集合，其余阶段候选应相同。

draw必须是独立新请求，缓存键包含draw_id，不能把一次响应复制成3次。断点恢复相同draw则读取原响应。API不承诺重复调用确定性；相同seed也不代表跨模型同随机性。记录实际参数与支持情况。

候选显示顺序按(seed,stage,draw)平衡/打乱，同一个draw的不同policy/model使用同一顺序；network集合不同则记录相对匹配。请求按固定随机调度交错发送模型和条件，减少时间漂移。

### C1：小规模动态smoke（C0后单独执行）

每模型一个20Agent世界，balanced/open、12tick，使用同一初始外生状态，两个世界分别运行，不把不同模型放在同一社会。目的是打通真实决策→状态变化→Idea→合作/退出→事件日志，不用于估计政策效应。沿用原tick语义，输出实际阶段次数，不把tick当成一次完整科研周期。

C1模型决定选题、query、Idea选择、邀请/接受与退出；生成/修订Idea使用同一世界的模型，任务schema分开。规则负责强制约束、合法候选、执行结果，不重写模型选择。只有事件实际发生时才计入事件指标；不能为了每项都有结果强制退出/完成。

C1预算单独估算与授权，不因C0通过自动发起。完整动态政策矩阵2模型×3seed×6制度=36世界留给下一轮，不在本轮自动运行。

## 5. 政策与观察协议

共享system prompt与语义一致的user观察；跨模型只允许接口格式适配，不允许为某模型单独优化科研策略提示。

政策数值沿用novelty/recognition的(0.5,0.5)、(1,0)、(0,1)，但recognition必须使用v02独立定义，不恢复为1-novelty。将实际奖励更新、资源约束与候选特征写入POLICY_SPEC.json，给模型解释数值及后果；不能只告诉标签“novelty”却隐瞒制度含义。

对于C0，政策只是同一固定决策场景的制度条件；不得声称实际经历了多轮奖励学习。对于C1，制度描述必须与引擎执行一致。外生候选生成在reward政策之间固定，避免规则事先替模型选好政策偏好的动作。

模型只能看到自己的合法观察、已公开他人信息、可见论文及预算；不暴露隐藏状态、其他条件标签、qrels、未来结果。论文正文作为数据，不执行其中指令。此轮无web工具调用、无代码执行工具或真实实验操作。

决策schema建议：

```json
{
  "action_id": "one allowed candidate ID",
  "reason_summary": "brief visible reason, not hidden reasoning",
  "evidence_ids": ["visible paper or observation IDs"]
}
```

按stage定义enum/合法组合，严格校验。无效ID、不可见引用、错误类型、截断、拒绝均分类记录。reason_summary仅用于审计，不将它视为模型真实内部推理。不要要求输出思维链。

## 6. API后端与模型配置

扩展现有backend，新增model registry而非单一全局MODEL变量；优先复用项目已支持的OpenAI-compatible chat适配器，并按用户选择的第二供应商实现对应原生接口。不得假设不同API都支持同一参数。

配置每模型：model_key、provider、base_url_env、api_key_env、requested_model_id、returned_model_id、endpoint_type、capabilities、temperature、top_p、seed_support、max_output_tokens、reasoning设置、价格币种与核对日期。

模型ID由用户实际可用账号决定，不在源码硬编码未经核对的“最新”名字，不用ChatGPT订阅名代替API ID。优先固定可用快照；只有滚动别名时标记model_version_unpinned，记录运行时间与服务端返回ID。

内部接口：decide(stage, observation, allowed_actions, request_key)、propose、revise。所有接口统一返回raw_response、validated_payload、usage、latency、provider_request_id、status。不同provider输出上限/tokenizer不完全可比，使用相同可见文本和语义上限，并记录差异，不能宣称完全算力匹配。

结构化输出受供应商与具体模型支持限制。能力适配层选择JSON Schema或JSON模式并本地校验；不静默丢参数。参考官方文档（执行时核对所选模型支持）：

- https://developers.openai.com/api/docs/guides/structured-outputs
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs

这些是协议参考，不是指定必须购买这两家服务。本轮实际使用两个用户可访问的模型即可。

## 7. 成本、错误、缓存与授权

提供离线doctor（仅检查环境变量是否设置，绝不打印key）、estimate和mock；live probe属于真实调用，也计入预算。

在用户未给具体模型/单价/预算时完成全部离线工程，输出NEEDS_USER_CONFIG.json列明缺项，不擅自选择付费配置。用户在本地填写models.local.json（不入git）与预算后，按AGENTS.md完成probe和estimate；已有对应live授权则执行，不重复索要。

预算同时限制max_logical_calls、max_http_attempts、max_input_tokens、max_output_tokens、max_total_cost和walltime；probe、重试、修复请求均占用。C0 base432逻辑决策不含probe和重试。金额上限必须有币种；价格未核对则不宣称费用可控，可只生成估算待配置。

请求前保守预留输出和输入费用；并发预算原子扣留；服务端usage缺失标usage_unknown，不填0并保守占用预算。C0可顺序执行降低复杂度。

429/5xx/timeout按明确退避最多重试2次；认证/模型不存在等立即停。JSON无效可最多一次格式修复请求，计为额外调用，保留首次失败；不反复重试直到产生偏好动作。失败不回退mock，不自动改用其他模型。C0失败保留missing；C1采用显式no_action并记录，不能默认规则决策却称LLM行为。

缓存键包含provider/model实际配置、提示版本、schema、观察/候选/证据/政策hash、world/agent/stage/draw、采样参数。恢复不能跨draw复用；重放只能读缓存、不得重新调用API。重放确定性不等于在线重新采样确定性。

raw请求/响应保留可复核研究内容，但授权header、key、账户标识等须剔除；不在日志记录密钥，不将含key配置打包。实际失败日志需保留状态而非吞错。

## 8. 冻结检索与真实语料适用范围

C0固定证据快照，检索非待比较因素。C1新增bm25_live_pilot检索配置，复用验证过的BM25参数/排序，关闭旧硬topic gate；不依赖未通过的soft方案。

36篇文献只覆盖memory/retrieval/science-evaluation的小范围；审计现有20Agent字段与topic，若原平台包含语料不支持领域，建立有版本的新pilot领域映射，仅在新配置限制到受支持领域，不能把空领域归咎模型。保留原领域设计。

同一query所有模型使用相同检索器、截止时间、返回上限、证据文本截断规则；引用必须来自可见文献。若证据少于生成所需最小数，记录insufficient_evidence并允许延期/no_action，不拿无关论文填满。

不对新自由query套用旧9条查询qrels计算“检索准确率”。C0有限query选择先足够；自由文本query生成可留到后续新实验。

## 9. 分析与统计单位

C0先输出合法响应率、首次通过率、修复率、拒绝率、缺失率、时延、token与费用，按模型/政策/阶段分层。

主行为指标：各stage动作分布；同一state、network下novelty/recognition对balanced的预定义动作特征均值差；邀请率和条件于邀请的跨领域比例分开；退出比例；query/选题差异率。候选动作特征必须预先冻结且不由本次模型自评生成。

闭合网络跨领域率为0是约束结果，不当成模型社会偏好。模型同样选择某动作不表示没有政策理解；三draw只作采样敏感性。

不同模型的政策响应比较用差中之差：
Delta_m = mean_state(mean_draw(Y_m,treatment - Y_m,balanced))；
Interaction = Delta_M1 - Delta_M2。

只在相同阶段和network中做reward政策配对。动作集合不同的closed/open不直接逐ID对齐，比较共通统计并解释集合约束。state/world seed为主要重复单位，draw/Agent/项目不是独立世界；3state仅探索，报告每state差值，不做显著性宣称。

C1只报告过程指标和账本：草案、项目、合并、退出、完成、单人/团队、贡献归一化产出及实际可见行为。无独立质量评价时quality字段为not_evaluated/null，不以模型自评分填补，不写质量调整产出“已完成”。可用引用ID有效率、去重代理辅助诊断，但ID有效不代表论证得到文献支持。

不增加LLM裁判作为本轮默认任务。制度直接决定的reward/pressure改变属于规则效应，与模型动作改变分开列示。

## 10. 建议文件与必要测试

建议新增或复用：model_registry.py、decision_backend.py、decision_schema.py、stage_c_frozen.py、stage_c_live.py、usage_ledger.py、configs/stage_c_mock.json、configs/stage_c_live.example.json、execute_stage_c.py。在CHANGELOG写真实路径映射，不只复制旧backend重命名。

测试必须覆盖：四阶段LLM选择实际进入事件/状态；规则选择不再二次覆盖；candidate人数与合法域；reward配对时nonpolicy观察一致；模型/政策/draw缓存隔离；限额包含失败和修复；无效输出不silent mock；可见引用校验；全世界初始化匹配；断点恢复/缓存重放；档案ZIP最终解压运行。

使用fake transport覆盖API协议、超时/拒绝/截断/usage缺失，不用真实API跑单元测试。完整Linux测试与AGENTS.md要求的mock重放照常执行，不把测试通过数固定为124。

## 11. CLI与交付

实现明确命令（下面是目标接口，现有同义命令可映射）：

```bash
python3 execute_stage_c.py doctor --config configs/stage_c_mock.json
python3 execute_stage_c.py estimate --config configs/stage_c_live.example.json
python3 execute_stage_c.py run --phase c0 --mode mock --output outputs_stage_c/c0_mock
python3 execute_stage_c.py probe --config configs/models.local.json
python3 execute_stage_c.py run --phase c0 --mode live --config configs/models.local.json --output outputs_stage_c/c0_live
python3 execute_stage_c.py replay --run-dir outputs_stage_c/c0_live --offline
```

probe/live只在模型、预算、授权齐备时执行；未齐备不妨碍完成其他工作。示例配置不能伪装为有效live配置。C1给出单独命令与独立预算。

交付PLAN_FROZEN、POLICY_SPEC、MODEL_MANIFEST、SNAPSHOT_MANIFEST、REQUEST_SCHEDULE、REQUEST_LOG（脱敏）、DECISIONS、ERRORS、USAGE_LEDGER、行为指标与配对差CSV、CHECKPOINTS、REPLAY_VALIDATION、ZIP_REPRODUCTION_VALIDATION、TEST_STATUS、STATUS、REPORT_ZH.md、源码差异清单和最终ZIP。顶层及必要嵌套checksum全部保留，旧ZIP不动。

STATUS区分engineering_complete、mock_complete、live_c0_complete、live_c1_complete、model_versions_pinned、independent_human_validation=deferred_by_user、quality_evaluation=not_performed、ready_for_scientific_claims=false。不要因尚未设置key把工程工作全部标failed，也不要因mock完成把live标true。

完成后反馈：修复了哪些打包问题、模型是否实际选择动作、432逻辑请求中成功/失败/修复/缓存数、实际费用、各政策行为差异、是否进入C1，以及下一步需用户配置的具体项。本轮若没有live授权/配置，则交付完整可运行工程及精确启动方式，不发起付费请求。
