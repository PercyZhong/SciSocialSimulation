# SciMirror Stage A 补充实验报告

本报告仅是合成证据条件下的工程与机制可测试性验证，不是独立科学质量评价或现实政策因果证据。

## 四个主条件

|corpus|ranker|calls|mean selected|shortfall rate|mean gold capacity coverage|
|---|---|---:|---:|---:|---:|
|corpus_expanded|stage_a_reference|324|3.0000|0.0000|0.9074|
|corpus_expanded|stage_a_supplement|324|3.0000|0.0000|0.9074|
|corpus_original|stage_a_reference|324|1.1667|1.0000|1.0000|
|corpus_original|stage_a_supplement|324|1.1667|1.0000|1.0000|

reference 与 supplement 使用同一冻结排序函数，行为相同；本轮改善来自新增的实质不同证据供给和独立 gold 口径，而非人为修改排序公式。

## Policy 链路

54 次启用路径端到端调用完成；在 18 个场景×历史×ranker 分母中，policy 产生所选集合变化的组数为 18。
政策特征/分数存在跨度的组数为 18，排序变化组数为 18，集合变化组数为 18；每条分数分解与原因见 policy_e2e_results.jsonl。
路径关闭后每个ranker的唯一输出集合数：{'stage_a_reference': 1, 'stage_a_supplement': 1}。评分单元表与生产特征分解见对应 JSONL。

## 每主题供给、返回、覆盖和不足

|corpus|ranker|topic|gold families|mean selected|capacity coverage|shortfall rate|
|---|---|---|---:|---:|---:|---:|
|corpus_expanded|stage_a_reference|agent_communication|7|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_reference|agent_memory|7|3.0000|0.8519|0.0000|
|corpus_expanded|stage_a_reference|agent_planning|6|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_reference|agent_tools|6|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_reference|learning_exploration|6|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_reference|learning_graph|6|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_reference|learning_policy|7|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_reference|learning_reward|7|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_reference|science_collaboration|7|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_reference|science_evaluation|6|3.0000|0.0370|0.0000|
|corpus_expanded|stage_a_reference|science_hypothesis|6|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_reference|science_retrieval|7|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_supplement|agent_communication|7|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_supplement|agent_memory|7|3.0000|0.8519|0.0000|
|corpus_expanded|stage_a_supplement|agent_planning|6|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_supplement|agent_tools|6|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_supplement|learning_exploration|6|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_supplement|learning_graph|6|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_supplement|learning_policy|7|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_supplement|learning_reward|7|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_supplement|science_collaboration|7|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_supplement|science_evaluation|6|3.0000|0.0370|0.0000|
|corpus_expanded|stage_a_supplement|science_hypothesis|6|3.0000|1.0000|0.0000|
|corpus_expanded|stage_a_supplement|science_retrieval|7|3.0000|1.0000|0.0000|
|corpus_original|stage_a_reference|agent_communication|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_reference|agent_memory|1|2.0000|1.0000|1.0000|
|corpus_original|stage_a_reference|agent_planning|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_reference|agent_tools|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_reference|learning_exploration|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_reference|learning_graph|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_reference|learning_policy|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_reference|learning_reward|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_reference|science_collaboration|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_reference|science_evaluation|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_reference|science_hypothesis|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_reference|science_retrieval|1|2.0000|1.0000|1.0000|
|corpus_original|stage_a_supplement|agent_communication|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_supplement|agent_memory|1|2.0000|1.0000|1.0000|
|corpus_original|stage_a_supplement|agent_planning|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_supplement|agent_tools|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_supplement|learning_exploration|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_supplement|learning_graph|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_supplement|learning_policy|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_supplement|learning_reward|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_supplement|science_collaboration|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_supplement|science_evaluation|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_supplement|science_hypothesis|1|1.0000|1.0000|1.0000|
|corpus_original|stage_a_supplement|science_retrieval|1|2.0000|1.0000|1.0000|

## Gold、主题重合与不足

全主题对行数：2376；去重错误对：0。
扩充语料去重仅在冻结 test 家族上评价：{'corpus': 'corpus_expanded', 'evaluation_split': 'test', 'evaluated_documents': 63, 'true_positive_pairs': 12, 'false_positive_pairs': 0, 'false_negative_pairs': 0, 'pairwise_precision': 1.0, 'precision_null_reason': None, 'pairwise_recall': 1.0, 'recall_null_reason': None}。校准家族不进入该精确率/召回率。
按预标注关系的主题对汇总：{'shared_evidence': {'n': 54, 'document_jaccard_mean': 0.0, 'gold_family_jaccard_mean': 0.0}, 'near_no_shared': {'n': 630, 'document_jaccard_mean': 0.009523809523809525, 'gold_family_jaccard_mean': 0.009523809523809525}, 'far': {'n': 1692, 'document_jaccard_mean': 0.01773049645390071, 'gold_family_jaccard_mean': 0.01773049645390071}}。共享 evidence 的主题对允许非零 Jaccard，集合相同但顺序不同单列。
原语料的不足被保留；扩充语料不填槽，逐阶段不足原因见 shortfall_attribution.csv。原语料 family 映射用于复现模板供给，不等同于外部科学 gold；独立内容卡/qrels 结论以扩充语料为准。

## 执行与成本

测试 59 项；E1 1296 次、E2 60 次（54启用+6关闭控制）、主题配对2376行；smoke 为20 Agent、balanced/open、12 tick。网络/HTTP/LLM/收费API均为0。

## 范围与待办

广义语义同义仍不在透明词法检索能力内。真实语料、独立标注者一致性与外部有效性留待后续阶段；0/1 条证据沿用现有 wait/low-evidence 行为，2/3 条证据只引用可见文献。
