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
`# 排查步骤`、`# 根因对照表`，章节内的步骤写成 `## 1. 步骤名称`。

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

前置检查还可以多写一项「需记录字段」，说明哪些回显要留给后面的步骤复用：

```
# 前置检查

## 1. 采集 SRv6 TE Policy 运行状态

- **CLI命令**：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`
- **跳转信息**：
  - 回显中不存在对应 endpoint/color 的 Policy → 根因定位为"SRv6 TE Policy 不存在"，结束排查
  - `Policy State` 为 `Up` 且 `List State` 为 `Up` → 隧道状态正常，结束排查
  - 其余情况 → 继续前置检查步骤 2
- **根因定位**：SRv6 TE Policy 不存在
- **需记录字段**（后续步骤复用）：`Policy State`、srlist 部分的 `List State`、`BFD State`、`Verification State`
```

## 排查步骤

默认顺序执行，每个步骤需要有以下信息：

1. **步骤名称**
2. **CLI命令：接口名使用全称，变量需要用 `<>` 包裹**
3. **跳转信息：根据CLI的回显，在何种情况下需要跳转到其它步骤，或者跳转到其它排障文档（顺序执行无需额外标注）**
4. **根因定位：**
   - 根据CLI的回显，可以推导出哪些根因，给出根因名称

```
# 排查步骤

## 3. 检查 SRv6 TE Policy 是否被 shutdown

- **CLI命令**：复用前置检查步骤 1 回显的 `Policy State` 字段
- **跳转信息**：`Policy State` 不为 `Down (Shutdown)` → 跳转步骤 4
- **根因定位**：`Policy State` 为 `Down (Shutdown)` → "SRv6 TE Policy 被 shutdown"
```

需要额外下发命令时这样写：

```
## 4. 检查 BFD 检测状态

- **CLI命令**：
  1. 复用前置检查步骤 1 回显 srlist 部分的 `BFD State` 字段
  2. 若 `BFD State` 为 `Down`，执行 `display bfd session srv6-segment-list <segment-list-id>`
- **跳转信息**：`BFD State` 不为 `Down` → 跳转步骤 5
- **根因定位**：`BFD State` 为 `Down` → "BFD 检测 Down"
```

补充要求：

- 步骤编号从 1 开始连续。
- **CLI命令**：判据来自前置检查已采集的回显时，写"复用前置检查步骤 N 回显的 X 字段"，
  **不要重复下发同一条命令**。
- **跳转信息**：判据字段与取值都用反引号标出；"跳转步骤 N"里的 N 必须真实存在。
  顺序执行到下一步时无需额外标注。最后一步要写清全部判据都不命中时的去向
  （判定"未找到根因"并输出已执行的检查项）。
- **根因定位**：只给根因名称，**不要写修复动作和复检命令**——它们统一放在根因对照表里，
  避免同一份修复在两处各写一遍、改了一处忘另一处。

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
- 「修复CLI和方法」写具体的 CLI 或操作；没有修复手段的根因写"无直接修复CLI"并说明只能定位，
  不要编命令。影响性大的修复在这一列注明影响与建议的操作时机。
- 「复检命令（可选）」写修复后用什么命令确认、通过标准是什么；没有复检手段时留 `-`。

## 命令行的格式约束

- 命令一律用反引号包成行内代码；多行配置序列用围栏代码块或表格内的 `<br>` 分行。
- 可变参数一律用尖括号 `<>`，不要用 `{}`、`[]`、`XXX` 或大写占位符。
- 参数名用小写英文，多个单词用连字符分隔：`<endpoint-ipv6>`、`<color-id>`、`<segment-list-id>`。
- **同一个参数在整篇 skill 里必须用同一个名字**。
- 接口名使用全称（写 `GigabitEthernet0/1/0`，不写 `GE0/1/0`）。

</template>
