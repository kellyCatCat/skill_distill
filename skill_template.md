# Skill 模板

这份模板定义 `excel_skill_distill_pipeline.py` 生成的 skill 长什么样。**它是 prompt 的一部分**：
流水线运行时读这个文件，拼进给模型的格式要求里，所以改模板改这里就行，不用动代码。

改完可以跑 `python3 excel_skill_distill_pipeline.py --validate excel_cases/sample_skill.md`
确认格式基准仍然符合模板；基准本身在 `excel_cases/sample_skill.md`。

下面 `<template>` 之间的内容会原样送进 prompt。

<template>

## 格式要求

skill 文档头部应为以下格式：

```
---
name: skill-name-in-English（例如：arp-learning-failure）
description: 故障现象 + 适用时机。例：基站掉站。出现掉站/站点不通/链路 Down 告警时使用。
---
```

以下章节从一级标题开始，每级增加一个 `#`。也就是说四个章节写成 `# 入参列表`、`# 前置检查`、
`# 排查步骤`、`# 根因对照表`，排查步骤内的每一步写成 `## 步骤N：步骤名称`。

四个章节顺序固定、缺一不可：`# 入参列表` → `# 前置检查` → `# 排查步骤` → `# 根因对照表`。

## 入参列表

请填写执行排查所需的全部入参信息，例如：

```
# 入参列表

| 信息 | 是否必填 | 说明 |
| --- | --- | --- |
| 网元ID | 是 | 网元的resId |
| endpoint IPv6 | 是 | 告警中 SRv6 TE Policy 的 endpoint 地址 |
| segment-list ID | 否 | 从前置检查的回显中提取，无需人工输入 |
```

- 「信息」列写参数的可读名称，去掉空格和连字符后要能和 CLI 里的参数名对上
  （`<endpoint-ipv6>` 对应「endpoint IPv6」，`<color-id>` 对应「color」）。
- 只能从回显里读出来、用户提供不了的参数，「是否必填」写「否」并在说明里注明来自哪一步。

## 前置检查

排障流程始于前置检查，格式参考下一节的排查步骤，但有以下额外要求：

1. CLI 之间必须是分步骤的线性执行关系，不可以有内部跳转
2. 所有 CLI 的参数不可以超出**入参列表**中的必填项目

前置检查各条写成有序列表，每条给出 CLI 命令与采集内容；能在采集阶段就判定的根因，
写一行「根因定位」：

```
# 前置检查

前置检查按顺序线性执行，仅用于采集后续排查所需的回显信息，不做跳转。

1. **查询目标 SRv6 TE Policy 状态**
   - CLI 命令：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`
   - 采集内容：`Policy State`、`List State`、`Verification State`、`BFD State`、Segment List ID 与 Policy 名称。
   - 根因定位：若回显为空或提示该 Policy 不存在，判定根因为"SRv6 TE Policy 不存在"，结束排查。

2. **采集 SRv6 静态配置**
   - CLI 命令：`display current-configuration configuration segment-routing-ipv6`
   - 采集内容：`segment-routing ipv6` 视图下的 SRv6 TE Policy、candidate path、segment list 及 SID 配置。
```

## 排查步骤

默认顺序执行，每个步骤需要有以下信息：

1. **步骤名称**
2. **CLI命令：接口名使用全称，变量需要用 `<>` 包裹**
3. **跳转信息：根据CLI的回显，在何种情况下需要跳转到其它步骤，或者跳转到其它排障文档（顺序执行无需额外标注）**
4. **根因定位：**
   - 根据CLI的回显，可以推导出哪些根因，给出根因名称

每个步骤用 `## 步骤N：名称` 作标题，四类信息写成有序列表：

```
# 排查步骤

默认按顺序执行。步骤1至步骤3、步骤4至步骤8直接复用前置检查已采集的回显，无需重复执行 CLI。

## 步骤4：检查 SRv6 TE Policy 是否被 shutdown

1. **步骤名称**：检查 SRv6 TE Policy 是否被 shutdown
2. **CLI 命令**：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（查看 `Policy State` 字段）
3. **跳转信息**：
   - `Policy State` 不为 `Down (Shutdown)`：顺序执行步骤5。
   - `Policy State` 为 `Down (Shutdown)`：定位根因，结束排查。
4. **根因定位**：
   - SRv6 TE Policy 被 shutdown
```

需要额外下发命令时，「CLI 命令」下列多条并注明触发条件：

```
## 步骤5：检查是否因 BFD Down 导致中断

1. **步骤名称**：检查是否因 BFD Down 导致中断
2. **CLI 命令**：
   - `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（查看 srlist 部分 `BFD State` 字段）
   - `display bfd session srv6-segment-list <segment-list-id>`（仅当 `BFD State` 为 `Down` 时执行）
3. **跳转信息**：
   - `BFD State` 不为 `Down`：顺序执行步骤6。
   - `BFD State` 为 `Down`：执行 BFD 会话查询确认后定位根因，结束排查。
4. **根因定位**：
   - bfd 检测 Down
```

补充要求：

- 步骤编号从 1 开始连续。
- **CLI 命令**：判据来自前置检查已采集的回显时，注明"复用前置检查步骤 N 回显"或"查看 X 字段"，
  **不要真的重复下发同一条命令**。
- **跳转信息**：判据字段与取值都用反引号标出；"跳转步骤 N"里的 N 必须真实存在，
  落到下一步时写"顺序执行步骤 N+1"。最后一步要写清全部判据都不命中时的去向
  （判定"未找到根因"，输出已执行的全部检查步骤及结果摘要，结束排查）。
- **根因定位**：只给根因名称，**不要写修复动作和复检命令**——它们统一放在根因对照表里，
  避免同一份修复在两处各写一遍、改了一处忘另一处。本步骤不产生根因时写"无"。

## 根因对照表

此表需要包含排查步骤包含的所有根因：

```
# 根因对照表

| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |
| --- | --- | --- | --- |
| SRv6 TE Policy 被 shutdown | `Policy State` 为 `Down (Shutdown)` | 在该 Policy 视图下执行 `undo shutdown` | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>` |
```

- 「根因」列的文字要和排查步骤「根因定位」里写的名称逐字一致，否则对不上。
- 「现象」写判定这个根因所依据的字段与取值。
- 「修复CLI和方法」**照抄步骤表「配置修复建议」列给的说法**，不要展开、不要补全。
  表里给的常常只是一句描述而不是命令，例如"BGP视图下配置ipv6-family sr-policy"，
  那就照写这一句；**不要把它展开成** `bgp <as-number>` / `ipv6-family sr-policy` /
  `peer <peer-ip> enable` 这样的配置序列——展开出来的命令是编的，还会带出
  `<as-number>`、`<peer-ip>` 这类入参列表里没有、用户也无处填的参数。
  表里那一格是空的，就写"无直接修复CLI"并说明只能定位。
  影响性大的修复在这一列注明影响与建议的操作时机。
- **正文里出现的每个 `<参数>` 都必须在入参列表里有对应行**。冒出没申报的参数，
  基本就说明那条 CLI 是编的。
- 「复检命令（可选）」写修复后用什么命令确认、通过标准是什么；没有复检手段时留 `-`。
- 全部步骤走完仍未命中任何故障特征时，加一行「未找到根因」，「修复CLI和方法」写
  **"输出已执行的全部检查步骤及结果摘要"**，到此为止。**不要写"转人工分析"、
  "收集诊断信息升级处理"、"上报工单"这类把问题转出去的动作**——agent 执行不了，
  排障链条会断在这里。

## 命令行的格式约束

**最重要的一条：只能使用源文档（步骤表）里出现过的 CLI，一条都不许自己生成。**

- 可用的命令来源只有步骤表的这几列：「命令行」「配置修复建议」「修复验证」「步骤详细描述」。
- **「回显」列不是命令来源**。回显是某台设备当时的输出，里面的 `bgp 100`、
  `peer 1::2 enable`、`segment-list 1`、`bgp route-learning acceleration enable`
  是那台设备的现状，不是让你照抄的配置模板。
- 表里没给命令、只给了一句修复方向（如"减少policy数量""BGP视图下配置ipv6-family sr-policy"），
  就**照实写这句方向**，不要补全成可执行的配置序列。补全出来的命令没有依据，
  在真实设备上多半是错的，而 agent 会当真执行。
- 表里那一格是空的，写"无直接修复CLI"并说明只能定位。

其余格式约束：

- 命令一律用反引号包成行内代码；多行配置序列用围栏代码块或表格内的 `<br>` 分行。
- 可变参数一律用尖括号 `<>`，不要用 `{}`、`[]`、`XXX` 或大写占位符。
- **参数名沿用步骤表里的写法，不要翻译、不要换词**。表里写 `<端口>` 就写 `<端口>`，
  不要改成 `<port-name>`、`<interface-name>` 之类——换了名字就和入参列表、和表里的
  命令都对不上了。只允许规整分隔符（`<endpointipv6>` → `<endpoint-ipv6>`）。
- **同一个参数在整篇 skill 里必须用同一个名字**。
- 接口名使用全称（写 `GigabitEthernet0/1/0`，不写 `GE0/1/0`）。

</template>
