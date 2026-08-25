# 项目说明

将 IPRAN 网络运维排障文档批量蒸馏为网管 agent 可用的 skill 文件。

- `skill_self_distill_pipeline.py`：主流水线。扫描源文档树（`result/v01/tree`），按"一级目录/二级目录"分组，调用大模型把每组文档合并转换为一个 skill（输出为 `<一级中文>/<二级中文>.md`），并生成 `skill_tree_structure.txt` 与 `conversion_report.json`。散落在一级目录下的文档会自动并入名称最相似的既有二级分组；若该一级目录没有二级目录，则全部合并为 `<一级名去前缀>故障案例.md`（如 `故障处理：QoS` → `QoS故障案例.md`），归并明细会在运行时打印。
- `skill_case_merge_pipeline.py`：增量并入流水线。读取 `cases/*.json` 里的新故障案例，先定位每个案例组应并入哪个既有 skill（或需要新建），再判定 `covered`（已覆盖）/ `append`（追加小节）/ `create`（新建 skill）并写入，最后输出 `reports/skill_change_report_<mm-dd>.md` 变更说明。`DRY_RUN=True` 只出报告不落盘。
- `skill_eval_optimize_pipeline.py`：评测优化流水线。读取 `evals/` 下的排障评测结果（自然语言，按 `对应SKILL：<路径>` 标记切分记录），把 skill 全文与评测结论交给大模型，定位是哪一步判据误导了排障 agent 并改掉，产出**整篇**优化后的 skill（改判据要动既有正文，所以不是追加而是整篇重写），输出 `reports/skill_optimize_report_<mm-dd>.md`。prompt 里内置 6 类缺陷的定位方法：单端判据、会提前放行的错误判据、缺失的排查落点、顺序不合理、同一根因散落重复、写进了执行不了的命令（评测里连续失败的命令绝不能留在 skill 里，更不能新增）。评测里除失败记录外还可以附「正确的诊断流程」（权威流程：告警名、逐步判据、方案生成、配置样例、对管控依赖），给了就以它为准校准正文，并逐条检查「转步骤X」指向的小节是不是真的是那一步该做的事——只给失败记录时模型只会在出错那步附近打补丁。marker 之前紧邻的标题行会归到该记录开头当上下文。评测里的 `对应SKILL：` 写 skill 库中的相对路径，允许省略 `故障处理：` 这类一级目录前缀（写 `IP路由/BGP故障案例.md` 能匹配到 `故障处理：IP路由/BGP故障案例.md`）；匹配顺序为 `EVAL_TARGET_OVERRIDES` → 精确路径 → 一级目录后缀 → 文件名，不唯一或找不到时报错并给出最接近的候选，**不会挑一个最像的去覆盖**。加 `--check` 只跑到匹配这一步，先确认每条评测落在哪篇 skill 上再花模型调用。这条流水线要做因果定位再整篇重写，是三条里最吃推理的，默认用 `qwen3.8-27b`（其余流水线用 `qwen3.6-27b`；原先默认的 MiniMax-M2.7 那个部署已下线，期间实测其 thinking 变体慢 11.8 倍、fix 更少、还把 Bad Peer AS 的 Error Code 写成 1）；`MAX_TOKENS` / `TIMEOUT` 可按流水线覆盖模型默认值。整篇覆盖会丢原文，所以落盘前校验篇幅与小节数不得缩到原文的 60% 以下，报告里也会写明匹配方式供审核。`DRY_RUN=True` 只出报告不落盘。
- 每篇 skill 都要带两节输出要求（约束写在 `skill_case_merge_pipeline.WRITING_RULES` 里，三条流水线共用）：**诊断结论**（故障对象 / 根因类型 / 原因（含故障对象的自然语言）/ 修复建议；定位不到时明确输出“未找到根因”）与**修复方案**（方案序号 / 方案说明 / 修复对象名 / CLI 序列，走管控接口的写路径与入参并注明不下发 CLI）。
- `apply_change_report.py`：把人工审过的 `reports/skill_change_report_*.md` 或 `reports/skill_optimize_report_*.md` 落盘到 skill 目录（追加块拼到文件末尾、新建块写成新文件、优化块整篇覆盖原文件）。改 `DRY_RUN=False` 重跑会让模型重新生成，落盘的就不是审过的那份，所以审完用这个脚本落盘。默认预演，加 `--apply` 才写文件，加 `--diff` 逐行看落盘前后的差异（整篇覆盖时只报字符数看不出改了什么，`--diff` 一定不写文件）；落盘是幂等的（追加前比对小节标题，覆盖前比对内容），重复执行不会写两遍。
- `model_config.py`：模型接入配置。地址与密钥放在**不入库**的 `.env`（模板见 `.env.example`，环境变量优先），这里按模型名登记调用参数：是否开思考、输出预算。三条流水线都经 `call_model_with_retry` 走到这里，传模型名即可，显式传 `api_url` 时优先。**思考开关必须按模型区分**——原先 payload 里写死的 `enable_thinking: False` 是给 qwen 关思考的，发给会思考的模型会把思考压掉；`thinking=True` 的模型不发这个字段。MiniMax-M2.7 那个部署已下线，眼下登记的都是 qwen 档（`QWEN36_*` / `QWEN38_*` 两组环境变量），thinking 那一档的处理仍保留给以后接入的模型。thinking 的推理 token 也占输出预算，所以那档 `max_tokens` 给到 32768，且回复里内联的 `<think>` 块会被剥掉（否则推理过程里的 ```json 围栏会被当成正式输出）。回复的读取兼容两种形态：普通 JSON，以及**无视 `stream: False` 一律返回 `text/event-stream` 的端点**（`MiniMax` 那个部署就是这样）——SSE 会被拼回完整回复，`content` 与 `reasoning_content` 分别累加，且显式按 UTF-8 解码（SSE 的 Content-Type 不带 charset，否则中文成乱码）。响应体不是 JSON 时报错会带上状态码、Content-Type 与正文开头，不再只抛一句 `Expecting value: line 1 column 1`。跑 `python3 model_config.py` 可自查当前解析出的配置（密钥打码），加 `--probe` 会再发一个 16 token 的最小请求确认链路真的通。
- `compare_models.py`：同一批评测跑多个模型并列比较，用于决定某条流水线该用哪个模型。按客观信号出对比表（是否被落盘前校验判失败、篇幅与小节保留率、fixes 条数、耗时），每个模型的报告分别写到 `reports/skill_optimize_report_<mm-dd>_<模型名>.md`；全程 DRY-RUN。数字之外仍需人工看 `fixes` 有没有点到评测真正暴露的那处判据。
- `validate_skills.py`：校验生成的 skill 是否完整合规（frontmatter、截断、残留引用、禁用短语等），存在 ERROR 时退出码为 1。
- `build_skill_tree.py`：按实际 skill 目录重新生成 `skill_tree_structure.txt`。这份树原是主蒸馏的副产物、且是按**源文档树的分组**写出来的，所以增量并入新建的 skill 不在里面，而刷新它本来得重跑整条蒸馏（要源文档树 + 几十次模型调用 + 覆盖全部 skill）。本脚本只扫目录、不调模型、只写这一个文件，跑完还会列出「蒸馏之后新增」和「报告里有但目录下没有」的 skill。渲染函数由主流水线共用，两边格式不会跑偏。加 `--dry-run` 只打印。**并入或新建 skill 之后记得跑一遍。**
- `extract_display_commands.py`：从 skill 里抽取 `display xxx` 查询命令，按源文件一一对照输出 json（`skills_distilled/07-27/<一级>/<二级>.md` → `cmd_distilled/07-27/<一级>/<二级>.json`，内容为 list of string）。行内代码与围栏代码块都扫，按出现顺序去重。
- `cases/`：新增故障案例的原始数据（入库）；`evals/`：排障评测结果（**不入库**，见 `.gitignore`）；`reports/`：变更说明，其中运行时生成的 `skill_change_report_*.md`、`skill_optimize_report_*.md` 不入库。

源文档树和生成的 skill 不入库（见 `.gitignore`）。

# Git 工作流

- 所有修改直接基于 `main` 开发，提交后合入（推送到）`main`。
- 不要创建新分支。
