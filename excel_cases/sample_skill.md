---
name: srv6-te-policy-down
description: SRv6 TE Policy Down（SR-Policy 隧道中断）。出现 SRv6 TE Policy down 告警、隧道不通、业务流量无法按 color 引流或倒换失败时使用。
---

## 入参列表

| 信息 | 是否必填 | 说明 |
| --- | --- | --- |
| 网元ID | 是 | 网元的 resId，用于登录目标设备 |
| endpoint IPv6 | 是 | 故障 SRv6 TE Policy 的 endpoint 地址，由告警携带 |
| color | 是 | 故障 SRv6 TE Policy 的 color 值，由告警携带 |
| segment-list ID | 否 | 从前置检查步骤 1 的回显中提取，无需人工输入 |
| BGP AS号 | 否 | 仅在修复"ipv6-family sr-policy 地址族未配置"时使用 |

## 前置检查

### 1. 采集 SRv6 TE Policy 运行状态

- **CLI命令**：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`
- **跳转信息**：
  - 回显中不存在对应 endpoint/color 的 Policy → 根因定位为"SRv6 TE Policy 不存在"，结束排查
  - `Policy State` 为 `Up` 且 `List State` 为 `Up` → 隧道状态正常，结束排查
  - 其余情况 → 继续前置检查步骤 2
- **根因定位**：SRv6 TE Policy 不存在
- **需记录字段**（后续步骤复用）：`Policy State`、srlist 部分的 `List State`、`BFD State`、`Verification State`、Segment List 名称与 ID

### 2. 采集 SRv6 静态配置

- **CLI命令**：`display current-configuration configuration segment-routing-ipv6`
- **跳转信息**：执行完成后进入排查步骤 1
- **根因定位**：无（仅采集配置，判定在排查步骤 1 进行）
- **需记录字段**：`srv6-te policy` 配置块、`candidate-path` 及其引用的 `segment-list`、`segment-list` 下的 `index ... sid ipv6` 条目

## 排查步骤

### 1. 判定静态配置完整性

- **CLI命令**：复用前置检查步骤 2 的回显，无需重复下发
- **跳转信息**：
  - `segment-routing ipv6` 下未配置该 SRv6 TE Policy 或未配置 candidate path → 判定为 BGP 动态下发场景，跳转步骤 2
  - 配置完整（含 candidate path、所引用的 segment list 及 SID）→ 跳转步骤 2
- **根因定位**：配置了 candidate path 但未引用 segment list，或所引用的 segment list 下无 SID 配置 → "SRv6 TE Policy 配置不完整"

### 2. 检查 BGP SR-Policy 地址族配置

- **CLI命令**：`display current-configuration configuration bgp`
- **跳转信息**：已配置 `ipv6-family sr-policy` 地址族 → 跳转步骤 3
- **根因定位**：未配置 `ipv6-family sr-policy` 地址族 → "ipv6-family sr-policy 地址族未配置"

### 3. 检查 SRv6 TE Policy 是否被 shutdown

- **CLI命令**：复用前置检查步骤 1 回显的 `Policy State` 字段
- **跳转信息**：`Policy State` 不为 `Down (Shutdown)` → 跳转步骤 4
- **根因定位**：`Policy State` 为 `Down (Shutdown)` → "SRv6 TE Policy 被 shutdown"

### 4. 检查 BFD 检测状态

- **CLI命令**：
  1. 复用前置检查步骤 1 回显 srlist 部分的 `BFD State` 字段
  2. 若 `BFD State` 为 `Down`，执行 `display bfd session srv6-segment-list <segment-list-id>`
- **跳转信息**：`BFD State` 不为 `Down` → 跳转步骤 5
- **根因定位**：`BFD State` 为 `Down` → "BFD 检测 Down"

### 5. 检查故障感知（SID 可达性）

- **CLI命令**：
  1. 复用前置检查步骤 1 回显 srlist 部分的 `List State` 与 `Verification State` 字段
  2. 若 `List State` 为 `Down (SID Stack Down)` 且 `Verification State` 为 `SID Unreachable`，执行 `display srv6-te policy source-sid` 确认 ISIS 拓扑中是否存在 srlist 中的 SID
- **跳转信息**：`List State` 不为 `Down (SID Stack Down)` 或 `Verification State` 不为 `SID Unreachable` → 跳转步骤 6
- **根因定位**：两个条件同时满足 → "故障感知检测 Down"

### 6. 检查 SRv6 TE Policy 规格超限

- **CLI命令**：
  1. 复用前置检查步骤 1 回显的 `Policy State` 字段
  2. 若 `Policy State` 为 `Down (Overrun)`，执行 `display paf | include SPEC_RES_SRV6POLICY_MAX_NUM` 查询设备支持规格
- **跳转信息**：`Policy State` 不为 `Down (Overrun)` → 跳转步骤 7
- **根因定位**：`Policy State` 为 `Down (Overrun)` → "SRv6 TE Policy 超限"

### 7. 检查 srlist 规格超限

- **CLI命令**：
  1. 复用前置检查步骤 1 回显 srlist 部分的 `List State` 字段
  2. 若 `List State` 为 `Down (Overrun)`，执行 `display paf | include SPEC_RES_SRV6POLICY_SEGLIST_GLOBAL_NUM` 查询设备支持规格
- **跳转信息**：`List State` 不为 `Down (Overrun)` → 判定"未找到根因"，输出已执行的全部检查步骤及结果摘要，结束排查
- **根因定位**：`List State` 为 `Down (Overrun)` → "srlist 超限"

## 根因对照表

| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |
| --- | --- | --- | --- |
| SRv6 TE Policy 不存在 | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>` 无对应 Policy 回显 | 确认告警携带的 endpoint/color 是否正确；若确需该隧道，按静态或 BGP 动态方式新建 Policy | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>` |
| SRv6 TE Policy 状态正常 | `Policy State` 为 `Up` 且 srlist 的 `List State` 为 `Up` | 无需修复。隧道状态正常，核对告警是否已自动清除 | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>` |
| SRv6 TE Policy 配置不完整 | 配置了 candidate path 但未引用 segment list，或 segment list 下无 SID | 补齐 segment list 与 SID 并在 candidate path 下引用：<br>`segment-routing ipv6`<br>` segment-list <segment-list-name>`<br>`  index <index> sid ipv6 <sid-ipv6>`<br>`srv6-te policy <policy-name> endpoint <endpoint-ipv6> color <color-id>`<br>` candidate-path preference <preference>`<br>`  segment-list <segment-list-name>`<br>影响性：配置变更可能引起流量切换，建议业务低峰期操作 | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>` |
| ipv6-family sr-policy 地址族未配置 | BGP 动态下发场景下 `display current-configuration configuration bgp` 中无 `ipv6-family sr-policy` | 在 BGP 视图下启用地址族：<br>`bgp <as-number>`<br>` ipv6-family sr-policy`<br>`  peer <peer-ipv6> enable` | `display bgp sr-policy ipv6 peer`，确认 peer 状态为 `Established` |
| SRv6 TE Policy 被 shutdown | `Policy State` 为 `Down (Shutdown)` | 取消 shutdown：<br>`srv6-te policy <policy-name> endpoint <endpoint-ipv6> color <color-id>`<br>` undo shutdown` | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>` |
| BFD 检测 Down | srlist 的 `BFD State` 为 `Down`，`display bfd session srv6-segment-list <segment-list-id>` 显示会话 Down | 执行 `tracert srv6-te policy endpoint-ip <endpoint-ipv6> color <color-id>` 定位故障点，按结果处理物理链路或接口配置；同时核对两端 BFD 配置 | `display bfd session srv6-segment-list <segment-list-id>` |
| 故障感知检测 Down | `List State` 为 `Down (SID Stack Down)` 且 `Verification State` 为 `SID Unreachable` | 执行 `display srv6-te policy source-sid`，根据回显中的 Producer/Node 信息检查对应 IGP 进程与链路状态，修复 SID 可达性（链路故障或 IGP 路由缺失） | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>` |
| SRv6 TE Policy 超限 | `Policy State` 为 `Down (Overrun)`，Policy 数量超过 `SPEC_RES_SRV6POLICY_MAX_NUM` | 无直接修复 CLI：删除不必要的 SRv6 TE Policy 配置，或升级设备规格/License | `display paf \| include SPEC_RES_SRV6POLICY_MAX_NUM` |
| srlist 超限 | srlist 的 `List State` 为 `Down (Overrun)`，Segment List 数量超过 `SPEC_RES_SRV6POLICY_SEGLIST_GLOBAL_NUM` | 无直接修复 CLI：删除不必要的 Segment List 配置，或升级设备规格/License | `display paf \| include SPEC_RES_SRV6POLICY_SEGLIST_GLOBAL_NUM` |
