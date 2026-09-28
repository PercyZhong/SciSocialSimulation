# SciMirror：学生预算双模型API购买、配置及Codex接入补充文档

核对日期：2026-09-27。本文补充Stage C执行文档，不改变432次C0实验矩阵。推荐属于低成本试验选型，不代表两个模型能力相当或能代表所有模型。

## 1. 本轮推荐

- M1：DeepSeek官方API，model=deepseek-flash。当前官方标明服务版本DeepSeek-V4.1-Flash。滚动别名无法保证永久固定，必须记录返回model、运行时间和可用fingerprint。
- M2：阿里云百炼Qwen Flash固定快照，model=qwen-flash-2025-07-28。优先华北2（北京）地域。虽非最新系列，但仍在官方文档列出，成本低、版本明确，适合首次流程和行为试验。调用前核实账号可用性。
- 两者均使用非思考模式、文本输入、无联网搜索、无外部工具。不要把一个模型的思考模式和另一个的非思考模式混为仅模型差异。
- 不购买GPU、云服务器、模型训练服务或Coding Plan来运行这轮API实验。继续使用已有Linux服务器。

## 2. 价格与预算

DeepSeek英文官方当前价，美元/百万token：未命中缓存输入低谷0.15、高峰0.30；输出低谷0.60、高峰1.20。此处不换算成所谓官方人民币价；中文价格页本次未成功读取，人民币结算以账号控制台为准。

Qwen Flash固定快照北京地域，单请求输入<=128K时，人民币/百万token：输入0.15元、输出1.50元。

估算假设：432次逻辑调用平均分给两个模型，每模型216次；每次2000输入token、300输出token；无缓存优惠、无失败重试、无额外思考token。

- 每模型输入432000token、输出64800token。
- DeepSeek高峰约0.20736美元，低谷约0.10368美元。
- Qwen约0.162元。

这是短JSON决策的示例估算，不是保证账单。完整论文、历史对话、修复请求和C1动态实验另算。先跑每模型1次probe及每模型4阶段各1次pilot（另计8次），查看实际usage再估432次。不修改正式提示或参数来追求行为结果；若协议修复，冻结新版本后正式运行。

建议程序硬预算：首次probe+8次pilot合计不超过人民币5元等值；C0单独总上限20元等值。美元和人民币必须分账，使用用户确认并记录日期的预算换算率，不能直接相加；也可分别设置美元/人民币预算。上述为建议，不是用户已经授权支出。

充值建议：控制台若支持，可各先充值10元；若不支持此金额，按平台最低合适额度操作。免费额度先核实型号和有效期，不把未确认赠额计入预算。充值余额不等于本轮授权支出，平台余额也不一定是严格停止阈值。

## 3. 开通DeepSeek

1. 在浏览器打开 https://platform.deepseek.com/ ，注册/登录官方账号。
2. 按页面要求完成必要账号步骤，在余额/充值区域小额充值；支付方式、币种和最低金额以本人控制台为准。
3. 在API keys页面创建专用key，备注SciMirror。
4. key仅保存在自己的Linux环境或密钥文件，不发送到聊天，不让Codex打印，不写入Git。
5. 配置base URL为https://api.deepseek.com，模型deepseek-flash。
6. 请求体明确设置thinking={"type":"disabled"}。不能依赖默认模式，官方当前默认为思考模式。

## 4. 开通阿里云百炼

1. 打开 https://bailian.console.aliyun.com/ ，用阿里云账号登录，按要求实名认证并同意开通百炼。
2. 选择计划使用地域，本文按华北2（北京）的价格估算。国际站或其他地域费用、权限和endpoint可能不同。
3. 创建API Key，给实验所在业务空间授权目标模型；在业务空间管理获取WorkspaceId。
4. 打开模型调用示例，选择qwen-flash-2025-07-28，复制该空间/地域的OpenAI兼容Base URL。
5. 当前官方首次调用文档的北京示例为 https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1 ，必须替换真实WorkspaceId。以本人控制台显示为准，不把旧通用地址硬套到所有账号。
6. 检查赠额与账号余额；在阿里云费用与成本中心充值并设置可用的费用提醒。普通按量调用即可，不需要先买训练或部署资源包。
7. 请求体设置enable_thinking=false，不启用搜索。

## 5. 在VS Code远程Linux终端设置环境

这些设置要放在实际执行实验的Linux环境，Windows本地设置不会自动传到远程。以下bash命令由用户自己在终端输入，输入key时不回显且不进入命令历史：

```bash
read -rsp 'DeepSeek API Key: ' DEEPSEEK_API_KEY
printf '\n'
export DEEPSEEK_API_KEY
read -rsp 'DashScope API Key: ' DASHSCOPE_API_KEY
printf '\n'
export DASHSCOPE_API_KEY
read -rp '粘贴控制台给出的百炼OpenAI兼容Base URL: ' QWEN_BASE_URL
export QWEN_BASE_URL
export DEEPSEEK_BASE_URL='https://api.deepseek.com'
```

只检查是否已设置，不输出真实值：

```bash
python3 - <<'PY'
import os
for name in ['DEEPSEEK_API_KEY', 'DASHSCOPE_API_KEY', 'QWEN_BASE_URL']:
    print(name, '已设置' if os.getenv(name) else '未设置')
PY
```

临时环境变量只作用于当前shell及子进程；重连需重设。Codex工具进程未必继承你另一个终端的变量，可由用户在同一终端手动启动实验；若需持久化，让Codex先实现读取仓库外0600权限密钥文件的loader，用户自己填写，Codex仅检查存在性。不要靠在聊天粘贴key解决环境问题。

## 6. 给Codex的配置要求

请在Stage C已有registry格式中写入以下信息；不要不看schema就覆盖models.local.json：

| 字段 | M1 | M2 |
|---|---|---|
| model_key | deepseek_flash | qwen_flash_snapshot |
| provider | deepseek | aliyun_bailian |
| endpoint_type | openai_compatible_chat | openai_compatible_chat |
| model | deepseek-flash | qwen-flash-2025-07-28 |
| api_key_env | DEEPSEEK_API_KEY | DASHSCOPE_API_KEY |
| base_url_env | DEEPSEEK_BASE_URL | QWEN_BASE_URL |
| 非思考参数 | thinking.type=disabled | enable_thinking=false |
| temperature | 0.7（probe验证支持） | 0.7（probe验证支持） |
| max_tokens | 512 | 512 |

0.7是本轮预设，不是最优值。API能力不支持时明确报告并在正式冻结前统一决定，不静默改变参数。先用短JSON+本地schema验证；JSON模式支持需probe确认，系统提示包含JSON要求。不要把DeepSeek JSON mode当成保证schema完全正确。

每次probe只发一个短请求，要求返回{"ok":true}；验证非空content、finish_reason、JSON解析和usage，打印状态/model/token数，不打印密钥或完整异常请求头。关闭SDK自动重试或把重试纳入预算；禁止同一请求无界重试。

model不存在/无权限/余额不足时停止该模型，输出错误类别和修复建议，不能自动换另一个模型污染对照。分别验证两个模型，之后才执行8次四阶段小样本，再执行正式C0。

正式432请求的draw、政策、snapshot、模型、提示与参数都进入缓存键。探测/小样本与正式run使用不同ID，不能用probe响应充正式样本。保持原Stage C断点恢复、预算和日志规则。

成本估算按实际账号价格；DeepSeek峰谷取请求时间和时区，预留按高峰；Qwen记录地域与快照价格。输出分模型、分币种账本。不把选择这两个模型的建议当作用户已批准付费调用。

## 7. 常见问题

- 401：key/账号或地域不匹配，检查变量加载，不回显key。
- 403或模型权限错误：检查业务空间和模型访问授权。
- 404或model not found：检查Base URL、WorkspaceId和模型ID，禁止自行改模型完成实验。
- 余额不足：确认对应官方平台余额，会员订阅或其他平台额度不会自动转入。
- JSON报错：先看是否截断或仍开思考模式，再检查协议；不要改政策提示来追求成功。
- 本地可用远程失败：检查Linux实际环境与网络，不只检查Windows。

## 8. 当前就能发给Codex的指令

“请按本文件补充Stage C模型registry和配置示例，使用DeepSeek官方deepseek-flash与阿里云百炼qwen-flash-2025-07-28，均为非思考模式。先做离线配置检查、mock和预算估算，保护密钥。真实调用仅在我明确授权对应预算后执行，先每模型1次probe，再每模型四阶段各1次，核对费用后运行正式432次C0；不自动进入C1。把所需环境变量、实际启动命令和当前缺项告诉我。”

## 9. 官方来源（执行时重新核对）

- DeepSeek首次调用：https://api-docs.deepseek.com/zh-cn/
- DeepSeek模型及英文价格：https://api-docs.deepseek.com/quick_start/pricing/
- DeepSeek思考开关：https://api-docs.deepseek.com/guides/thinking_mode/
- Qwen Flash模型/快照/地域价格：https://help.aliyun.com/zh/model-studio/qwen-flash
- 百炼首次调用及WorkspaceId：https://help.aliyun.com/zh/model-studio/first-api-call-to-qwen
- 百炼API Key：https://help.aliyun.com/zh/model-studio/get-api-key
