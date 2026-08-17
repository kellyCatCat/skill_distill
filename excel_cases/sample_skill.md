---
name: srv6-te-policy-down
description: SRv6 TE Policy 隧道 Down。出现 SRv6 TE Policy Down 告警、隧道不通、Policy/List State 非 Up 或业务流量在 SRv6 TE Policy 上中断时使用。
---

# 入参列表

| 信息 | 是否必填 | 说明 |
| --- | --- | --- |
| 网元ID | 是 | 网元的 resId，用于确定登录并执行 CLI 的设备 |
| endpoint IPv6 | 是 | SRv6 TE Policy 的目的端点 IPv6 地址，对应 `endpoint` 参数 |
| color ID | 是 | SRv6 TE Policy 的 color 值，与 endpoint 共同唯一标识一条 Policy |
| segment-list ID | 否 | Segment List 编号，从前置检查步骤1的回显中获取，用于 BFD 会话查询 |
| policy 名称 | 否 | SRv6 TE Policy 名称，从前置检查步骤1或步骤2的回显中获取，用于修复阶段 |

# 前置检查

前置检查按顺序线性执行，仅用于采集后续排查所需的回显信息，不做跳转。

1. **查询目标 SRv6 TE Policy 状态**
   - CLI 命令：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`
   - 采集内容：`Policy State`、`List State`、`Verification State`、`BFD State`、Segment List ID 与 Policy 名称。
   - 根因定位：若回显为空或提示该 Policy 不存在，判定根因为"SRv6 TE Policy 不存在"，结束排查。

2. **采集 SRv6 静态配置**
   - CLI 命令：`display current-configuration configuration segment-routing-ipv6`
   - 采集内容：`segment-routing ipv6` 视图下的 SRv6 TE Policy、candidate path、segment list 及 SID 配置。

3. **采集 BGP 配置**
   - CLI 命令：`display current-configuration configuration bgp`
   - 采集内容：是否存在 `ipv6-family sr-policy` 地址族及其 peer 使能情况。

# 排查步骤

默认按顺序执行。步骤1至步骤3、步骤4至步骤8直接复用前置检查已采集的回显，无需重复执行 CLI。

## 步骤1：判定隧道当前状态

1. **步骤名称**：判定隧道当前状态
2. **CLI 命令**：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（复用前置检查步骤1回显）
3. **跳转信息**：
   - `Policy State` 与 `List State` 均为 `Up`：隧道状态正常，结束排查。
   - `Policy State` 不为 `Up`，或 `Policy State` 为 `Up` 但 `List State` 不为 `Up`：顺序执行步骤2。
4. **根因定位**：无（本步骤仅做状态判定）。

## 步骤2：检查静态配置的 SRv6 TE Policy 完整性

1. **步骤名称**：检查静态配置的 SRv6 TE Policy 完整性
2. **CLI 命令**：`display current-configuration configuration segment-routing-ipv6`（复用前置检查步骤2回显）
3. **跳转信息**：
   - `segment-routing ipv6` 下未配置该 SRv6 TE Policy 或未配置 candidate path：跳转步骤3（按 BGP 动态下发场景继续排查）。
   - 配置完整（含 candidate path、引用的 segment list 及 SID）：跳转步骤3。
   - 配置了 candidate path 但未引用 segment list，或 segment list 下无 SID 配置：定位根因，结束排查。
4. **根因定位**：
   - SRv6 TE Policy 配置不完整

## 步骤3：检查 BGP 动态下发场景的地址族配置

1. **步骤名称**：检查 BGP 动态下发场景的地址族配置
2. **CLI 命令**：`display current-configuration configuration bgp`（复用前置检查步骤3回显）
3. **跳转信息**：
   - 已配置 `ipv6-family sr-policy` 地址族：顺序执行步骤4。
   - 未配置 `ipv6-family sr-policy` 地址族：定位根因，结束排查。
4. **根因定位**：
   - ipv6-family sr-policy 地址族未配置

## 步骤4：检查 SRv6 TE Policy 是否被 shutdown

1. **步骤名称**：检查 SRv6 TE Policy 是否被 shutdown
2. **CLI 命令**：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（查看 `Policy State` 字段）
3. **跳转信息**：
   - `Policy State` 不为 `Down (Shutdown)`：顺序执行步骤5。
   - `Policy State` 为 `Down (Shutdown)`：定位根因，结束排查。
4. **根因定位**：
   - SRv6 TE Policy 被 shutdown

## 步骤5：检查是否因 BFD Down 导致中断

1. **步骤名称**：检查是否因 BFD Down 导致中断
2. **CLI 命令**：
   - `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（查看 srlist 部分 `BFD State` 字段）
   - `display bfd session srv6-segment-list <segment-list-id>`（仅当 `BFD State` 为 `Down` 时执行，`<segment-list-id>` 取自上一条命令回显）
3. **跳转信息**：
   - `BFD State` 不为 `Down`：顺序执行步骤6。
   - `BFD State` 为 `Down`：执行 BFD 会话查询确认后定位根因，结束排查。
4. **根因定位**：
   - bfd 检测 Down

## 步骤6：检查是否因故障感知 Down 导致中断

1. **步骤名称**：检查是否因故障感知 Down 导致中断
2. **CLI 命令**：
   - `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（查看 srlist 部分 `List State` 与 `Verification State` 字段）
   - `display srv6-te policy source-sid`（仅当命中故障感知条件时执行，用于确认 ISIS 拓扑中是否存在 srlist 中的 SID）
3. **跳转信息**：
   - `List State` 不为 `Down (SID Stack Down)`，或 `Verification State` 不为 `SID Unreachable`：顺序执行步骤7。
   - `List State` 为 `Down (SID Stack Down)` 且 `Verification State` 为 `SID Unreachable`：定位根因，结束排查。
4. **根因定位**：
   - 故障感知检测 Down

## 步骤7：检查 SRv6 TE Policy 是否超限

1. **步骤名称**：检查 SRv6 TE Policy 是否超限
2. **CLI 命令**：
   - `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（查看 `Policy State` 字段）
   - `display paf | include SPEC_RES_SRV6POLICY_MAX_NUM`（仅当 `Policy State` 为 `Down (Overrun)` 时执行，用于确认设备规格）
3. **跳转信息**：
   - `Policy State` 不为 `Down (Overrun)`：顺序执行步骤8。
   - `Policy State` 为 `Down (Overrun)`：定位根因，结束排查。
4. **根因定位**：
   - SRv6 TE Policy 超限

## 步骤8：检查 SRv6 TE Policy srlist 是否超限

1. **步骤名称**：检查 SRv6 TE Policy srlist 是否超限
2. **CLI 命令**：
   - `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（查看 srlist 部分 `List State` 字段）
   - `display paf | include SPEC_RES_SRV6POLICY_SEGLIST_GLOBAL_NUM`（仅当 `List State` 为 `Down (Overrun)` 时执行，用于确认设备规格）
3. **跳转信息**：
   - `List State` 为 `Down (Overrun)`：定位根因，结束排查。
   - `List State` 不为 `Down (Overrun)`：判定"未找到根因"，结束排查，并输出已执行的全部检查步骤及结果摘要。
4. **根因定位**：
   - srlist 超限
   - 未找到根因

# 根因对照表

| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |
| --- | --- | --- | --- |
| SRv6 TE Policy 不存在 | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>` 无对应 Policy 回显 | 确认 endpoint/color 入参是否正确；若确需该隧道，按规划新建静态 Policy 或确认 BGP 控制器是否已下发：<br>`segment-routing ipv6`<br>`srv6-te policy <policy-name> endpoint <endpoint-ipv6> color <color-id>` | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>` |
| SRv6 TE Policy 配置不完整 | 已配置 candidate path，但未引用 segment list，或 segment list 下无 SID 配置 | 补充缺失的 candidate path、segment list 或 SID 配置（配置变更可能引起流量切换，建议业务低峰期操作）：<br>`segment-routing ipv6`<br>`segment-list <segment-list-name>`<br>`index <index> sid ipv6 <sid-ipv6>`<br>`srv6-te policy <policy-name> endpoint <endpoint-ipv6> color <color-id>`<br>`candidate-path preference <preference>`<br>`segment-list <segment-list-name>` | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>` |
| ipv6-family sr-policy 地址族未配置 | BGP 配置中不存在 `ipv6-family sr-policy` 地址族，控制器下发的 Policy 无法生效 | 在 BGP 视图下启用 IPv6 SR-Policy 地址族：<br>`bgp <as-number>`<br>`ipv6-family sr-policy`<br>`peer <peer-ip> enable` | `display bgp sr-policy ipv6 peer`（确认 peer 状态为 `Established`） |
| SRv6 TE Policy 被 shutdown | `Policy State` 为 `Down (Shutdown)` | 取消 Policy 的 shutdown 状态：<br>`srv6-te policy <policy-name> endpoint <endpoint-ipv6> color <color-id>`<br>`undo shutdown` | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（确认 `Policy State` 为 `Up`） |
| bfd 检测 Down | srlist 的 `BFD State` 为 `Down`，`display bfd session srv6-segment-list <segment-list-id>` 显示会话未 Up | 定位链路故障点后处理物理链路或接口配置：<br>`tracert srv6-te policy endpoint-ip <endpoint-ipv6> color <color-id>`<br>根据 tracert 结果修复对应节点/链路 | `display bfd session srv6-segment-list <segment-list-id>` |
| 故障感知检测 Down | `List State` 为 `Down (SID Stack Down)` 且 `Verification State` 为 `SID Unreachable` | 检查并修复 SID 可达性问题（链路故障或 IGP 路由缺失）：<br>`display srv6-te policy source-sid`<br>根据回显中的 Producer 与 Node 信息，检查对应 IGP 进程及链路状态 | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（确认 `Verification State` 恢复正常） |
| SRv6 TE Policy 超限 | `Policy State` 为 `Down (Overrun)`，Policy 数量超过 `SPEC_RES_SRV6POLICY_MAX_NUM` 规格 | 无直接 CLI 修复。删除不必要的 SRv6 TE Policy 配置以释放资源，或升级设备规格 | `display paf \| include SPEC_RES_SRV6POLICY_MAX_NUM` |
| srlist 超限 | srlist 的 `List State` 为 `Down (Overrun)`，Segment List 数量超过 `SPEC_RES_SRV6POLICY_SEGLIST_GLOBAL_NUM` 规格 | 无直接 CLI 修复。删除不必要的 Segment List 配置以释放资源，或升级设备规格 | `display paf \| include SPEC_RES_SRV6POLICY_SEGLIST_GLOBAL_NUM` |
| 未找到根因 | 全部检查步骤执行完毕，各字段均未命中已知故障特征 | 不做根因判定。输出已执行的全部检查步骤及结论摘要，并附 `Policy State`、`List State`、`BFD State`、`Verification State` 的实际取值与原始回显片段，交用户判断 | — |
