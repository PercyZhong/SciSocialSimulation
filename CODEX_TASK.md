# 给VS Code Codex的执行任务

请在包含本文件的项目根目录完成SciMirror v0.1的本地部署和首轮实验。已有完整代码，请先运行与检查，不要从零另写一套平台。

1. 阅读README.md及docs/EXPERIMENT.md，确认研究边界与6阶段事件语义。
2. 检查当前终端操作系统、Python路径和版本。VS Code Remote中使用远程解释器；要求Python>=3.11。
3. 执行`python execute.py`（Windows可用`py -3.11 execute.py`，Linux可用`python3 execute.py`）。该文件创建项目内.venv，运行测试并执行20Agent/30tick/3seed/6条件mock实验。
4. 不修改全局环境或已有科研环境；没有Python时先报告具体缺失。无需第三方pip包或GPU。
5. 若代码失败，根据异常定位修复。保留模式标签和研究约束；不能删测试、弱化引用校验、把mock冒充LLM或把不同seed当同一反事实初始状态。修复后重跑相应测试与完整mock实验。
6. 检查最新outputs目录的status.json为completed，summary.csv共18行数据；每个最终状态有20Agent且tick=30；所有分支的源fork_state_hash相同（每个seed内部）；每个分支完整性重放已通过。
7. 检查REPORT.md、paired_effects.csv、blind_review.csv、review_key_private.csv、manifest.json、usage.json。报告路径、测试结果和实际运行模式。
8. 此任务默认只执行mock。若用户已另行授权真实LLM试跑且已经配置端点和模型，先执行doctor --config configs/llm_pilot.json --probe与estimate，随后执行llm_pilot。缺少端点或模型时只列出所缺配置，不索取明文密钥，不输出或读取展示任何密钥内容。不要未经用户授权启动15000调用的research批次。
9. 本版本的API适配器已通过本地HTTP测试，但用户真实端点须单独验证。mock结果为工程验证，词汇新颖性、领域熵和网络干预不能被改写成真实创新或社会学结论。
10. 最终用中文给出：环境、测试通过数量、输出目录、20Agent和18个条件运行的验收结果、是否真实调用LLM、进入真实数据实验还缺什么。

入口：execute.py。一键脚本只执行可离线重现的部署、测试、mock实验，不执行真实收费API调用。
