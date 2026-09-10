# SciMirror v0.2 升级与验收报告

日期：2026-09-10  
本地执行环境：Windows / Python 3.12.7  
权威目标环境：Linux / Python 3.11+（仍需在远程服务器复验）

## 结论

四项机制升级均已实现并通过本地 mock 工程验收。v0.1 入口、配置、样例结果和既有缓存没有被覆盖。全部归档使用合成语料与 deterministic mock，HTTP 尝试为 0，未调用收费 API，也未验证真实 LLM 端点。

这份结果证明代码路径、状态机、计量守恒、分支与重放按协议工作；不证明现实科研政策有效，也不代表生成内容具有真实学术质量。

## 四项升级

### 1. Recognition 与 novelty 解耦：通过

- `topics.py` 从截点前语料建立冻结主题统计快照，采用多标签分数计数、alpha 平滑和固定 peak 归一化。
- 最终 `community_attention_proxy` 由固定分类器识别的主题关注度计算；`lexical_novelty_proxy` 继续单独计算。
- 合成语料主题预处理覆盖 90/90，未知 0；测试证明改变主题频率会改变 recognition，但不会改变同一语料上的 novelty scorer。

### 2. Policy 进入行为通路：通过

- `policy.py` 统一实现 topic、retrieval、idea_selection、invitation、exit 五条通路及效用/概率审计。
- 每条通路可单独关闭；`no_exit_policy` 保留 exit 动作空间，只关闭其政策项。
- `all_policy_paths_off` 同时中和制度反馈、提示中的政策、缓存语义和审计标签。6 个 seed×network 组的去标签终态哈希检查全部一致。
- policy 进入效用本身是建模假设；mock 中产生的差异不能外推为现实行为规律。

### 3. closed/open 候选人数匹配：通过

- `candidate_pool.py` 初始化时生成 policy-independent 冻结日程，默认 K=3。
- closed 严格 3 名同领域；open 严格 1 名同领域与 2 名其他领域；无重复、无自身。
- offered 与 actionable 分开记录，不可用槽位不后补。主实验候选审计 300 行，数量和配额检查通过。
- 候选身份、历史关系和邀请负荷仍可能不同；K 匹配不等于所有混杂完全消除。

### 4. Outputs、项目和贡献归一化：通过

- 初始候选、选定草案、项目、版本和最终成果使用不同稳定 ID。
- `Project` 支持 active → completed/abandoned/merged/censored；合并保留原草案与投入，退出支持中性接任或废弃。
- 追加式 effort ledger 包含失败、合并和退出投入；每项成果贡献份额和为 1，社会总份额等于 final outputs。
- 主实验共记录 1,440 个干预期项目启动、420 个合并、79 个废弃、941 个完成；总投入 51,164 模拟单位。这里是 18 个分支的累计计数，不是单一世界统计。

## 文件映射

| 职责 | 实现 |
|---|---|
| 冻结主题/recognition | `data/topics.json`, `scimirror/topics.py` |
| policy 通路与审计 | `scimirror/policy.py`, `scimirror/v02_engine.py` |
| 固定候选日程 | `scimirror/candidate_pool.py` |
| 项目状态与不变量 | `scimirror/v02_state.py`, `scimirror/v02_engine.py` |
| 投入和贡献份额 | `scimirror/accounting.py`, `scimirror/v02_metrics.py` |
| 分支、输出、消融 | `scimirror/v02_experiment.py` |
| 哈希链与重放 | `scimirror/events.py`, `v02_validate.py` |
| 入口和配置 | `v02_run.py`, `execute_v02.py`, `configs/v02_*.json` |
| 定向测试 | `tests/test_v02.py` |

## 自动测试

- 修改前 v0.1 基线：12/12 通过。
- 修改后全套：23/23 通过，其中旧版 12 项继续通过，v0.2 新增 11 项通过。
- 覆盖 recognition 解耦、截止期、五通路关闭、政策泄漏、K=3 日程、offered/actionable、退出/接任、合并账本、份额守恒、并发顺序和 v0.2 backend schema。

## Mock 实验与重放

| 归档 | seed | 分支 | tick | summary | trajectories | 验收 |
|---|---:|---:|---:|---:|---:|---|
| smoke | 3 | 18 | 12 | 18 | 18 | 18 个终态直接重放一致 |
| main | 3 | 18 | 30 | 18 | 72 | 18 个终态、共同 fork、直接重放全部通过 |
| 六组消融 | 每组3 | 108 | 30 | 每组18 | 每组72 | 每组18/18 归档重放通过 |
| diagnostic | 10 | 60 | 30 | 60 | 240 | 60/60 归档重放通过 |

主实验的 18 个终态均包含 20 Agent 且 tick=30；同 seed 的六分支共同 fork 哈希一致。事件日志包含实际发生的 420 次项目合并、191 次退出解析和 941 次完成。所有运行的 `usage.json` 均记录 `http_attempts=0`。

## 主实验描述性结果

三个 seed 的平均完成项目数分别为：balanced/closed 51.33、novelty/closed 53.00、recognition/closed 51.67、balanced/open 52.67、novelty/open 53.00、recognition/open 52.00。对应每 100 投入成果均值约为 1.806、1.858、1.811、1.856、1.874、1.829。

这些数字仅描述 deterministic mock 与合成语料下的模拟内部结果。样本只有 3 个世界，不能把 Agent 或项目当作独立样本，也不能据此声称哪种现实科研政策更优。配对均值和 bootstrap 区间位于每个归档的 `paired_effects.csv`。

## 调用成本与真实后端

- smoke 逻辑生成估算：630；mock HTTP=0。
- main 或单组消融：2,250；mock HTTP=0。
- 10-seed 诊断：7,500；mock HTTP=0。
- `v02_llm_pilot.json` 仅估算为 210 次逻辑调用、最多 210,000 completion tokens 配额单位、HTTP 尝试上限 250；未发出请求。

真实 LLM 尚未验证。必须在 Linux 上设置服务端点和模型、先做本地/服务端兼容预检、复核预算并获得明确授权；mock 成功不能替代真实 endpoint、schema 或学术有效性验证。

## 归档位置

- smoke：`outputs_v02/delivery_20260909/smoke/`
- 主实验：`outputs_v02/delivery_20260909/main/`
- 六组消融：`outputs_v02/delivery_20260909/ablations/`
- 完整 10-seed 诊断：`outputs_v02/delivery_20260910/diagnostic_10seed/`
- 最终总体验收：`outputs_v02/delivery_20260910/DELIVERY_VALIDATION_FINAL.json`

`outputs_v02/delivery_20260909/diagnostic_10seed/` 是此前进程中断留下的未完成目录，没有顶层 completed 状态；已原样保留且不纳入验收。

## 剩余限制

- 尚未在用户远程 Linux 环境执行，因此不能把 Windows 本地结果标为 Linux 权威验收。
- 未使用真实文献、真实 LLM 或外部盲评；主题规则和词汇新颖性都是透明但粗糙的代理。
- 固定候选人数只控制机会数量与预设学科配额，不能自动平衡候选身份、关系或邀请负荷。
- 3-seed 主实验与 10-seed 诊断是探索性规模，没有预注册功效保证或多重比较校正。
