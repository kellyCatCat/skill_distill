# 新增故障案例并入 skill 的改动说明

来源：`cases/故障补充场景.json`（由 `故障补充场景.pptx` 提取，37 页，含 16 行概览表 + 30 条场景条目）。

本次改动新增了一条增量并入流水线 `skill_case_merge_pipeline.py`：它先判断每个新案例是否已被既有 skill 覆盖，未覆盖的再决定是"在既有 skill 上追加小节"还是"新建 skill"，最后输出变更说明。

> 说明：已生成的 skill 库（`skills_distilled/<mm-dd>/`）和源文档树都不在仓库里，蒸馏用的模型接口也只在内网可达，所以本文给出的是**案例侧的处置方案**；在你的环境执行一次流水线后，会生成带实际改动内容的 `reports/skill_change_report_<mm-dd>.md`，那份才是逐条落地的最终结果。

## 一、流水线做了什么

| 阶段 | 动作 |
| --- | --- |
| 载入 | 读案例 JSON，剔除"仅有标题的补充页"和标注"已废弃 / 已从场景基线中去除"的条目，其余按`故障类型`归成案例组 |
| 定位 | 把既有 skill 清单（路径 + description + 小节标题）和案例摘要给模型，判定案例组应并入哪个既有 skill，或需要新建；新建时强制复用既有一级目录名 |
| 合并 | 同一目标 skill 的多个案例组合成一次调用，连同该 skill 全文交给模型，产出 `covered` / `append` / `create` 判定与内容 |
| 应用 | 主进程串行写文件：`append` 追加小节到既有 skill，`create` 新建 skill 文件；`covered` 不动 |
| 报告 | 输出 `reports/skill_change_report_<mm-dd>.md`，含变更总览表、每处改动的完整内容、已覆盖清单、失败项、未并入条目 |

写入前有一道硬校验：追加内容不得带 frontmatter、必须含 `## ` 小节；新建内容必须以 frontmatter 开头。不合规就不落盘，只记进报告。变更说明写在 `reports/` 而不是 skill 目录，避免被 `validate_skills.py` 当成 skill 校验出 ERROR。

生成内容沿用主蒸馏流水线的写作约束：删除"联系技术支持/提交工程师"类步骤、跨分类引用写成 `[一级/二级.md]`、不照搬大段回显。另外针对这批案例补了一条：PPT 里的内部讨论批注（"—— 待确认…"、"请刘瑞…"、人名工号）不写进 skill。

## 二、需要并入的案例：18 条，14 个案例组

`修复方案`列标注该组案例是否给出了可执行的修复动作；仅能定位到根因的，skill 里只写到根因判定为止。

| # | 案例组（故障类型） | 根因（来源页） | 触发告警 | 修复方案 | 预期落点 |
| --- | --- | --- | --- | --- | --- |
| 1 | ISIS 邻居状态异常 | ISIS System ID 冲突（p5） | IS-IS Adjacency Changed | 有（NCE-IP 分配 System ID） | 追加到既有 IS-IS 类 skill |
| 2 | OSPF 邻居状态异常 | 两端 IP 不在同一网段（p7） | OSPF Neighbor State Changes | 有（重分配 IP，用户选左/右端为准） | 追加到既有 OSPF 类 skill |
| 3 | BGP 邻居状态异常 | Peer IP 不匹配 / as-number 不匹配 / router-id 冲突（p9）；路由环路（p29） | Bgp Peer Backward Transition 等 | 部分（Peer IP 与环路仅定位） | 追加到既有 BGP 类 skill |
| 4 | BFD 状态异常 | 会话对端 IP 不匹配（p10）；对端标识符不匹配（p12） | BFD Session Down | 部分（标识符可自动修，IP 仅定位） | 视 skill 库有无 BFD 分类：追加或新建 |
| 5 | LDP 隧道故障 | LDP 未使能（p13） | LDP 会话 Up→Down | 有（全局 + 接口使能 mpls ldp） | 追加到既有 MPLS/LDP 类 skill |
| 6 | RSVP-TE 隧道故障 | 人工关闭隧道（p15） | MPLS Tunnel Down 等 | 有（undo shutdown / 管控 API） | 追加到既有 MPLS 隧道类 skill |
| 7 | SR-TE 隧道故障 | 人工关闭隧道（p16） | sr-te policy status has been changed | 有（同上） | 视 skill 库有无 SR 分类：追加或新建 |
| 8 | SR-Policy 隧道故障 | 人工关闭 SR-Policy（p17） | hwSrPolicyDown | 有（管控 API 置 admin-status up） | 同上 |
| 9 | SRv6-Policy 隧道故障 | 人工关闭 SRv6-Policy（p19） | hwSrPolicyDown | 有（同上） | 同上 |
| 10 | PWE3 故障 | PW 远端 IP 不匹配（p20）；MTU 不一致（p21） | PW VC Down | 有（远端地址取对端 LSR ID；MTU 对齐） | 追加到既有 L2VPN 类 skill |
| 11 | VPLS 故障 | PW 远端 IP 不匹配（p22）；MTU 不一致（p23） | VPLS VC Down | 有（同上，作用在 VSI peer） | 追加到既有 L2VPN 类 skill |
| 12 | L2EVPN 故障 | 两端 Service ID 不匹配（p24） | hwEvpnEvplAlarmDown | 有（remote-service-id 取对端 local） | 追加到既有 L2VPN/EVPN 类 skill |
| 13 | 1588v2 时钟异常 | P/E 模式不匹配（p26） | PTP_ATTR_MISMATCH | 有（对齐上游 2~3 跳，缺省推 E2E） | 大概率新建（时钟同步是新主题） |
| 14 | 链路性能越限 | 切片链路带宽利用率过高（p31） | IGP 链路质差 / 带宽利用率越限 | 有（切片链路带宽调整 API） | 大概率新建（性能类是新主题） |

"预期落点"是按故障主题给出的判断，不是最终结论——真实的既有 skill 清单在你的环境里，由流水线定位阶段读取后决定，结果会写进运行时报告。

## 三、未并入的 12 条

**标注为已下线的（5 条）**，脚本按`已废弃`/`已从场景基线中去除`自动跳过：

- p6 ISIS 两端 IP 不在同一网段 —— 实验证实不会导致邻居 down，标注"已废弃"
- p33 隧道故障：LSR ID 不匹配
- p34 SR-TE 隧道故障：LSR ID 不匹配
- p35 SR-TE 隧道故障：SID 冲突
- p36 SR-Policy 隧道故障：SID 冲突
- p37 SRv6-Policy 隧道故障：SID 冲突

（后 5 条均标注"已从场景基线中去除"。注意概览表里仍把"SID 冲突"列为 SR-TE/SR-Policy/SRv6-Policy 的诊断项，与详情页的下线标注矛盾，需要你确认以哪个为准。）

**只有标题、没有正文的补充页（7 条）**，无法蒸馏出步骤：

- p11 物理上线路错连，影响 OSPF 场景
- p18 SR-Policy 隧道故障：人工关闭 SR Policy
- p25 L2EVPN 故障：L2EVPN 两端 Service ID 不匹配
- p27 BGP Peer Down 故障：ISIS 路由环路
- p28 案例：ISIS 路由环路
- p30 L2VPN 业务故障：二层环路

其中 p18、p25 的主题已由 p17、p24 的完整页覆盖，无影响；p27/p28/p30 对应概览表里的"ISIS 路由环路"和"二层环路"两个根因，**目前只有概览行、没有排障步骤**——"二层环路"因此完全没有落地内容，需要补充原始素材。

## 四、案例数据里发现的问题

跑之前建议先确认，否则会带进 skill：

1. **p19 故障类型写成"Rv6-Policy隧道故障"**（应为 SRv6-Policy）。按`故障类型`分组会把它单独成组，可能被判成新建 skill 而不是并入 SR 隧道类。
2. **p16 SR-TE 的根因判据写着"若存在则根因为人工关闭 RSVP-TE 隧道"**，是从 p15 复制粘贴留下的错误，应为 SR-TE。
3. **p21 / p23 的`根因类型`仍写"PW 远端 IP 地址不匹配"，但正文讲的是 MTU 不一致**，归类和小节标题都会受影响。
4. **p26 的`方案生成`只有"1、通过"**（PPT 里被截断），真正的修复思路在`备注`里（往上找 2~3 跳对齐 P/E 模式，不统一时优先 E2E）。流水线会把`备注`一并送给模型，但这条建议在源数据里补全。
5. p20/p22 的备注里有"建议改成 MTU 不一致，请刘瑞新增一页"这类内部讨论，已在提示词里明确要求不写进 skill。

## 五、怎么跑

```bash
python3 skill_case_merge_pipeline.py
python3 validate_skills.py skills_distilled/$(date +%m-%d)
```

默认参数在 `skill_case_merge_pipeline.py` 文件末尾的 `main()` 调用里改：`CASES_PATH` 换案例文件，`SKILL_DIR` 指向要并入的 skill 目录，`DRY_RUN=True` 只出报告不落盘（建议先跑一次 dry-run 核对定位结果）。有处理失败项时退出码为 1，失败原因写在报告的"定位阶段失败的案例组"和总览表里，改完重跑即可——`append` 会重复追加，重跑前记得用 git 回滚 skill 目录。
