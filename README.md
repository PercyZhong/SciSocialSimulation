# SciMirror：20个Agent科研社会模拟实验

## v0.3 检索、盲评和质量计量升级

v0.3 保留 v0.1/v0.2 的入口、状态机和旧实验归档，使用独立的 `execute_v03.py`、`configs/v03_*.json`、`outputs_v03/` 与 schema 0.3。新增能力包括：

- 将选题 ID 展开为冻结的主题描述/关键词，按 topic、field、memory 的固定权重进行词法相关性召回；policy 只重排共同的相关性合格池，低证据时允许检索短缺而不以无关文献补位。
- 导出冻结查询探针、实时检索审计、文献重合、主题匹配、相关性、近重复与短缺诊断。
- 从单次运行的干预期完成项目冻结总体，以 `seed×policy×network×solo/team` 分层抽样；公开盲评包与私有条件映射隔离，支持人工评分导入、覆盖/一致性/分歧和抽样质量分析。
- 区分项目合并、成员离队、废弃、完成、失败投入、成果近重复和抽样质量调整产出。评分缺失时质量估计保持 incomplete，不作零填补。

本地离线命令：

```bash
python3 execute_v03.py check
python3 execute_v03.py test
python3 execute_v03.py retrieval-diagnose --config configs/v03_mock_main.json
python3 execute_v03.py run --config configs/v03_mock_main.json
python3 execute_v03.py export-review --run-dir outputs_v03/YOUR_RUN --config configs/v03_review.json --review-dir outputs_v03/YOUR_REVIEW_PACKAGE
python3 execute_v03.py review-estimate --review-dir outputs_v03/YOUR_REVIEW_PACKAGE
python3 execute_v03.py analyze --run-dir outputs_v03/YOUR_RUN --review-dir outputs_v03/YOUR_REVIEW_PACKAGE
python3 execute_v03.py validate --run-dir outputs_v03/YOUR_RUN --review-dir outputs_v03/YOUR_REVIEW_PACKAGE --diagnosis-dir outputs_v03/YOUR_DIAGNOSIS
```

`export-review`、`review-estimate`、`analyze` 和 `validate` 均不调用真实模型。`review-run` 默认明确拒绝执行，因为本项目没有获得外部评分预算授权。待评包不是完成的独立评分；只有导入有效的人工或明确授权的外部评分后，才可能计算完整质量指标。完整协议见 [docs/EXPERIMENT_V03.md](docs/EXPERIMENT_V03.md)，本次交付见 [V03_UPGRADE_REPORT.md](V03_UPGRADE_REPORT.md)。

人工评分可以分批导入，例如先导入 reviewer_a、再导入 reviewer_b；程序累计保存有效评分和原始记录，并拒绝覆盖已经存在的 reviewer-review 配对。分析会生成逐条件/逐成果类型四维质量、五套预设权重敏感性，以及在 seed 层配对的政策、网络和交互质量效应。评分不完整时，这些总体估计、均值和区间保持空值并报告实际样本量。

## v0.2 四项机制升级

## v0.2 四项机制升级

v0.1 入口、配置和旧结果保持不变。v0.2 使用独立入口 `v02_run.py` / `execute_v02.py`、显式 `schema_version: "0.2"` 和独立输出目录 `outputs_v02/`，主要变化如下：

1. `community_attention_proxy` 来自截点前语料的冻结主题频率，不再定义为 `1 - novelty`；词汇新颖性明确命名为 `lexical_novelty_proxy`。
2. policy 通过可独立关闭、可审计的通路进入选题、检索、候选 Idea 选择、合作邀请和项目退出；`all_policy_paths_off` 还会固定制度反馈，并自动检查政策泄漏。
3. 每个 `(seed, cycle, leader)` 预先冻结 K=3 候选日程。closed 为 3 名同领域候选；open 为 1 名同领域加 2 名其他领域候选。后续不可用只减少 actionable 数，不补位。
4. 草案、项目、版本和最终成果使用不同 ID；项目支持 completed / abandoned / merged / censored，投入使用追加式账本，最终成果按实际投入分配守恒份额。

离线完整验收：

```bash
python3 --version                    # 必须 >= 3.11
python3 execute_v02.py --suite       # 测试、主实验、六组消融、10-seed诊断
```

只运行默认 3-seed、20-Agent、30-tick 主实验：

```bash
python3 execute_v02.py
```

分步运行与只估算真实后端调用：

```bash
.venv/bin/python v02_run.py test
.venv/bin/python v02_run.py run --config configs/v02_mock_full.json
.venv/bin/python v02_run.py estimate --config configs/v02_llm_pilot.json
```

`estimate` 不联网。不得在没有端点预检、预算复核和用户明确授权时运行 `v02_llm_pilot.json`。v0.2 协议见 [docs/EXPERIMENT_V02.md](docs/EXPERIMENT_V02.md)，本次实现与验收见 [UPGRADE_REPORT.md](UPGRADE_REPORT.md)。mock 与合成语料只用于工程和机制验证，不是现实科研政策证据。

## v0.1 原型说明

完整可运行原型，Python 3.11+，Windows / Linux / VS Code Remote可用。运行与测试只依赖Python标准库，不需要GPU、CUDA、PyTorch、数据库服务或pip安装。20个Agent是20份独立状态和决策上下文，可共享一个模型服务，并不需要加载20个模型。

**离线mock模式用于软件验证。真实LLM通过兼容Chat Completions的HTTP服务接入。内置语料全部为合成夹具；本项目尚不是经过真实科研生态校准的平台。**

## 交给VS Code Codex执行

解压到独立目录，用VS Code打开包含execute.py的scimirror文件夹。把CODEX_TASK.md交给Codex，或者输入：

> 请阅读当前项目CODEX_TASK.md和docs/EXPERIMENT.md，执行python execute.py；运行失败请修复并重跑测试。完成后报告20个Agent、18个条件运行的结果路径和重放验证情况。先完成mock实验；不要把mock结果描述为真实科研发现。真实API配置和后续步骤请遵守CODEX_TASK.md。

无需逐段复制代码。execute.py会建立.venv、运行测试、完成默认实验。运行入口run.py提供其他命令。

## 目录

```text
scimirror/
  execute.py                 # 给Codex的一键执行文件
  CODEX_TASK.md               # 给Codex的完整执行任务
  run.py                     # setup/test/run/estimate/doctor/replay
  requirements.txt           # 明确零第三方运行依赖
  .env.example               # 环境变量模板，不自动加载
  .vscode/                   # 调试配置与任务
  configs/
    mock.json                # 20Agent,30tick,3seed,6条件
    llm_pilot.json           # 20Agent,12tick,1seed,6条件
    llm_research.json        # 真实语料,20seed;手动启动
  scimirror/
    state.py                 # Agent/World数据类、状态不变量
    common.py                # 散列、独立随机流、原子JSON写入
    corpus.py                # 语料验证、年份过滤、词汇检索
    backend.py               # mock/HTTP、响应校验、重试、缓存
    engine.py                # 六阶段社会模拟、资源/容量冲突
    events.py                # 日志、检查点和完整性重放
    metrics.py               # 行为代理指标、配对bootstrap
    experiment.py            # 前缀分叉、6条件实验、报告
  data/demo_papers.jsonl     # 90条合成英文夹具
  tests/test_core.py          # 自动测试
  docs/EXPERIMENT.md          # 完整研究与实现边界
  outputs/                   # 执行时生成，不覆盖旧目录
  cache/                     # LLM输入/输出缓存，不提交git
```

## Windows：安装和运行

安装Python 3.11或更新版本，在终端检查：

```powershell
py -3.11 --version
py -3.11 execute.py
```

如果只安装了其他满足版本要求的Python，使用`python execute.py`。不需要激活PowerShell脚本，也不需要改执行策略。一键完成后可直接使用环境解释器：

```powershell
.\.venv\Scripts\python.exe run.py test
.\.venv\Scripts\python.exe run.py run --config configs/mock.json
```

VS Code执行“Python: Select Interpreter”，选择`.venv\Scripts\python.exe`。Python扩展用于调试支持；Codex执行终端命令不依赖调试扩展。

## Linux / VS Code Remote

所有命令都在远程终端执行，使用远程Python，不能把本地Windows的.venv复制到服务器。

```bash
python3 --version
python3 execute.py
.venv/bin/python run.py test
.venv/bin/python run.py run --config configs/mock.json
```

建议通过Git同步源码；如果使用`rsync`或SFTP，排除`.venv/`、`cache/`和`outputs/`，在Linux上重新创建虚拟环境。项目提供`.gitattributes`将脚本和配置固定为LF行尾，避免Windows检出设置影响Linux脚本。

首次部署或服务器环境变化后先做不联网预检：

```bash
python3 execute.py
.venv/bin/python run.py doctor --config configs/mock.json
.venv/bin/python run.py doctor --config configs/llm_pilot.json
```

`doctor`可以从项目外的任意工作目录调用。它会报告实际解释器路径、平台、项目根目录、配置、后端、过滤后的语料数量、写权限、调用估算和LLM环境变量是否设置；不会显示环境变量内容。只有显式加入`--probe`才会访问远程模型。

若系统未提供venv模块，由有权限的用户安装对应Python venv系统包。若已有Conda：

```bash
conda create -n scimirror python=3.11 -y
conda activate scimirror
python run.py test
python run.py run --config configs/mock.json
```

Conda路线无需再运行execute.py创建嵌套环境。不要修改其他研究项目使用的环境。

## 首次真实LLM连接

使用你已有的、支持`POST /v1/chat/completions`的服务。BASE_URL填写到`/v1`，不能重复填写`/chat/completions`。模型名称必须是服务实际接受的名称。Python客户端不承诺所有兼容服务都接受相同参数；当前发送model/messages/temperature/max_tokens，send_seed默认关闭。非兼容接口需要在backend.py增加适配。

Windows当前PowerShell终端设置：

```powershell
$env:SCIMIRROR_BASE_URL = "https://YOUR_PROVIDER/v1"
$env:SCIMIRROR_MODEL = "YOUR_SERVED_MODEL"
# 如需鉴权，用隐藏输入，避免把真实密钥写进终端命令历史
$secure = Read-Host "API key" -AsSecureString
$env:SCIMIRROR_API_KEY = [System.Net.NetworkCredential]::new("", $secure).Password
.\.venv\Scripts\python.exe run.py doctor --config configs/llm_pilot.json --probe
.\.venv\Scripts\python.exe run.py estimate --config configs/llm_pilot.json
.\.venv\Scripts\python.exe run.py run --config configs/llm_pilot.json
```

Linux当前Bash终端设置：

```bash
export SCIMIRROR_BASE_URL="https://YOUR_PROVIDER/v1"
export SCIMIRROR_MODEL="YOUR_SERVED_MODEL"
read -rsp 'API key: ' SCIMIRROR_API_KEY
export SCIMIRROR_API_KEY
.venv/bin/python run.py doctor --config configs/llm_pilot.json --probe
.venv/bin/python run.py estimate --config configs/llm_pilot.json
.venv/bin/python run.py run --config configs/llm_pilot.json
```

本地服务可设`http://localhost:8000/v1`；无鉴权可不设API_KEY。环境变量必须在运行Python的同一终端或其父进程中设置，其他终端不会自动同步。`.env.example`只是模板，本项目不自动读.env。

doctor默认仅显示变量是否设置，不显示内容；--probe才会实际请求模型。真实端点从未在交付环境验证，因为没有你的端点和凭据。HTTP适配器通过本地测试服务验证了请求解析、缓存与token计数。未配置API时不会静默降级为mock。

## 真实文献配置

将你有权使用并已核验的英文文献导出为`data/papers.jsonl`。一行一篇，格式：

```json
{"id":"your_verified_id","title":"真实英文标题","abstract":"真实英文摘要","year":2024,"field":"agents","synthetic":false}
```

这里的标题/摘要是字段说明，不能作为研究语料。至少3篇；推荐第一轮使用数量可管理且领域覆盖合理的真实语料，之后再扩展。要求唯一ID，整数year，boolean synthetic，非空英文title/abstract。加入source_url/doi可用于追溯，解析器会保留。年份必须小于cutoff_year。

`configs/llm_research.json`强制拒绝synthetic=true；它不会联网下载文献，缺文件会明确失败。模型预训练中的未来知识不受本地时间过滤约束，需要在研究中承认和测量。

正式运行前：

```bash
python run.py estimate --config configs/llm_research.json
python run.py run --config configs/llm_research.json
```

默认15000次未缓存逻辑调用，不要在首次连接时直接执行。按你的实际预算修改seeds/ticks/max_calls。ticks和branch_tick必须是6的倍数，后者小于前者。

## 结果读取

每次运行输出到新的`outputs/日期时间/`。打开REPORT.md、summary.csv、paired_effects.csv。18条汇总=3世界seed×6条件，不是18个Agent。反事实重复单位是世界seed。

CSV可在VS Code或Excel中打开；blind_review.csv给评审者，review_key_private.csv留给实验者。不要把制度标签/奖励值给盲评人员。真实文献引用有效性仍需人类核验。

重放某分支（替换时间目录）：

```bash
python run.py replay --events outputs/YOUR_RUN/seed_42/novelty_open/events.jsonl
```

重放检查哈希链并恢复最后一个已提交快照，不产生模型调用。完整实验已自动对每个分支执行重放并比较最终状态散列。

## 常见失败

- `python`找不到：Windows用`py -3.11`；Linux先确认远程解释器已安装。
- Python版本过低：使用独立Python 3.11+环境。
- BASE_URL/MODEL缺失：在当前执行终端配置；别把密钥写入代码。
- HTTP 401/403：检查服务鉴权；404：检查base URL和model。
- JSON/schema错误：确认模型支持按提示生成纯JSON；调整max_tokens或模型，不能以mock结果替代。
- 调用预算耗尽：usage.json查看消耗；提高预算是显式实验配置决定。
- 输出目录存在：指定新目录，本程序不覆盖日志。
- 某tick失败：失败状态和已有checkpoint保留；修正问题后相同配置新建输出目录重跑，可命中已有缓存。

## Stage A 冻结状态检索实验

Stage A 在不修改科研社会机制的前提下，并存比较真实的 `baseline_v03` 与显式启用的 `stage_a_fixed`。旧 `configs/v03_mock_main.json` 继续使用 `relevance_gated`；只有新配置 `configs/v03_mock_stage_a_fixed.json` 会让生产检索接口进入修复版。

Linux（Python 3.11+）完整离线执行：

```bash
python3 execute_stage_a.py check --config configs/stage_a_frozen_retrieval.json
python3 execute_stage_a.py test
python3 execute_stage_a.py all --config configs/stage_a_frozen_retrieval.json
```

也可依次运行 `calibrate`、`run`、`analyze` 和 `validate`，并用 `--output`/`--run-dir` 指向同一目录。`run` 支持输入 hash 一致时按 case 恢复；输入或配置变化时会拒绝复用旧目录。使用 `--dry-run` 只打印计划，不发起实验。默认配置强制 `allow_network=false`、`allow_llm_calls=false`，核心矩阵是 108 个冻结状态上的 648 次确定性检索调用，不是 648 个社会模拟世界。

当前本地验收输出位于 `outputs_stage_a/stage_a_delivery_20260913/`。其中 `STAGE_A_REPORT.md` 是中文结论，`DELIVERY_VALIDATION_STAGE_A.json` 是逐项验收，`stage_a_delivery.zip` 是便携归档。它们只验证合成 fixture 的工程行为，不构成真实文献检索质量或现实政策效应证据。

## Stage A 补充实验

补充实验在不改变排序公式和科研社会机制的前提下，增加内容不同的合成证据、隔离的 gold 家族、policy 选择链路探针、测试分区去重评价，以及一个 20-Agent/12-tick 生产接口 smoke。默认严格离线，禁止网络、LLM 和收费 API。

Linux（权威运行环境）执行：

```bash
python3 execute_stage_a_supplement.py check --config configs/stage_a_supplement.json
python3 execute_stage_a_supplement.py prepare --config configs/stage_a_supplement.json
python3 execute_stage_a_supplement.py test
python3 execute_stage_a_supplement.py all --config configs/stage_a_supplement.json --output outputs_stage_a_supplement/<run_id>
```

Windows 可将 `python3` 换成当前 Python 3.11+ 环境的 `python`。`all` 运行 1296 次冻结状态检索、54 次启用 policy 探针、6 次关闭控制和 2376 行复用式主题对比较；这些是确定性合成工程验证，不是随机社会世界重复或现实因果证据。结果以 `DELIVERY_VALIDATION_STAGE_A_SUPPLEMENT.json` 和 `stage_a_supplement_delivery.zip` 为准。

## 评价修补与真实文献小试验

评价补丁使用完整的 `(dataset, ranker, topic, memory)` 分层重新计算旧结果，不运行检索或重新校准。真实文献小试验默认要求30–50篇文献、6–9条人工查询、family/canonical映射、两名固定评分者，并对整个有限语料逐查询盲评。缺少输入时只生成空模板并保持pending。

```bash
python3 execute_evaluation_patch.py all --previous-run outputs_stage_a_repair/<existing_run> --output outputs_evaluation_patch/<run_id>
python3 execute_stage_b_retrieval.py init --config configs/stage_b_retrieval_small.json --output outputs_stage_b/pilot_small_001
python3 execute_stage_b_retrieval.py --help
```

Stage B 的后续命令必须使用run内冻结配置。评分采用追加修订历史；输入变化必须新建run。部分标注的 `analyze` 返回状态 `partial_analysis` 和退出码2，不能解释为真实实验完成。完整命令和待输入字段见交付目录中的 `NEXT_INPUTS.md` 与 `ANNOTATION_GUIDE_ZH.md`。

## Stage B 软重排探索实验

该实验从已冻结且完整评分的小试验中迁移原始评分历史，不修改标签。它固定比较 BM25、两个历史排序器、lambda=0 等价对照以及两个 `lambda=0.10` 软重排臂；新排序器只对 BM25 top20 做非门控重排，不自动替换生产 Agent 的检索路径。

```bash
python3 execute_stage_b_improvement.py run \
  --config configs/stage_b_improvement.json \
  --source-run /path/to/frozen_stage_b_run \
  --output outputs_stage_b_improvement/<run_id>
python3 execute_stage_b_improvement.py validate --run-dir outputs_stage_b_improvement/<run_id>
python3 execute_stage_b_improvement.py reproduce --run-dir outputs_stage_b_improvement/<run_id> --output outputs_stage_b_improvement/<reproduced_id>
python3 execute_stage_b_improvement.py finalize --run-dir outputs_stage_b_improvement/<run_id>
```

执行严格离线，不调用 LLM、收费 API 或文献服务。查询属于重复使用的探索性 pilot；现有评分为 AI 辅助、人类抽查和提交，独立人工验证已延期，因此任何结果均保持 `ready_for_scientific_claims=false`。

候选规模诊断在上述冻结结果上比较 K20 与全语料候选，并按需运行固定 K20 归一化分母的反事实。它不改变 lambda、画像、标签或生产检索路径：

```bash
python3 execute_stage_b_candidate_diagnostic.py run \
  --config configs/stage_b_candidate_diagnostic.json \
  --source-run outputs_stage_b_improvement/<previous_run> \
  --output outputs_stage_b_candidate_diagnostic/<run_id>
python3 execute_stage_b_candidate_diagnostic.py validate --run-dir outputs_stage_b_candidate_diagnostic/<run_id>
python3 execute_stage_b_candidate_diagnostic.py reproduce --run-dir outputs_stage_b_candidate_diagnostic/<run_id> --output outputs_stage_b_candidate_diagnostic/<reproduced_id>
python3 execute_stage_b_candidate_diagnostic.py finalize --run-dir outputs_stage_b_candidate_diagnostic/<run_id>
```

## 验证边界与下一步

本版优先证明系统实现正确：状态隔离、资源/团队约束、共同前缀、模拟复现、日志重放和模式明确。实验设计、指标定义与后续研究要求见docs/EXPERIMENT.md。mock和合成语料不得支持真实社会机制结论；LLM+真实语料也需要独立评审和校准。

参考接口文档：
- https://docs.python.org/3.11/library/venv.html
- https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/

交付验收记录：VALIDATION.json。sample_results/包含本次完整mock运行的报告、数据及日志；你自己的新运行写入outputs/。交付环境实际为Linux/Python 3.12.14，Windows脚本尚未在Windows实机执行。
