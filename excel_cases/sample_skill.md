---
name: srv6-te-policy-down
description: SRv6 TE Policy Down（隧道中断）。出现 SRv6 TE Policy Down / SRv6 隧道不通 / SR-Policy 状态异常 / 承载于 SRv6 TE Policy 的业务流量中断等告警时使用，覆盖配置缺失、BGP 地址族未配置、被 shutdown、BFD Down、SID 不可达、Policy 或 srlist 规格超限等场景。
---

# SRv6 TE Policy Down 排障指南

## 入参列表

| 信息 | 是否必填 | 说明 |
| --- | --- | --- |
| 网元ID | 是 | 网元的 resId，用于确定 CLI 下发的目标设备 |
| endpoint IPv6 | 是 | 告警中 SRv6 TE Policy 的目的节点 IPv6 地址（endpoint） |
| color ID | 是 | 告警中 SRv6 TE Policy 的 color 值 |
| segment-list ID | 否 | Segment List 标识，由前置检查 1 的回显中获取，无需用户提供 |
| segment-list 名称 | 否 | Segment List 名称，由前置检查 2 的回显中获取，无需用户提供 |

## 前置检查

前置检查为线性执行的信息采集，不做分支判断，三条命令按序全部执行完毕后进入排查步骤。

### 前置检查 1：采集 SRv6 TE Policy 运行状态

- **CLI命令**：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`
- **采集字段**：
  - Policy 是否存在（是否有回显）
  - `Policy State`（关注取值：`Up` / `Down (Shutdown)` / `Down (Overrun)`）
  - srlist 部分的 `List State`（关注取值：`Up` / `Down (SID Stack Down)` / `Down (Overrun)`）
  - srlist 部分的 `BFD State`（关注取值是否为 `Down`）
  - srlist 部分的 `Verification State`（关注取值是否为 `SID Unreachable`）
  - srlist 的 Segment List ID / 名称及其中的 SID 列表
- **执行完毕后**：进入前置检查 2。

### 前置检查 2：采集 SRv6 静态配置

- **CLI命令**：`display current-configuration configuration segment-routing-ipv6`
- **采集字段**：`segment-routing ipv6` 视图下是否存在目标 endpoint/color 对应的 srv6-te policy、其 candidate-path、所引用的 segment-list 名称及 segment-list 下的 SID 配置。
- **执行完毕后**：进入前置检查 3。

### 前置检查 3：采集 BGP 配置

- **CLI命令**：`display current-configuration configuration bgp`
- **采集字段**：是否存在 `ipv6-family sr-policy` 地址族及其下的 peer 使能配置。
- **执行完毕后**：进入排查步骤。

## 排查步骤

按序执行；任一步已定位并修复且复检通过后，后续步骤停止。

### 步骤 1：判定 SRv6 TE Policy 是否存在及整体状态

- **CLI命令**：无（基于前置检查 1 回显判定）
- **跳转信息**：
  - 若 Policy 存在但 `Policy State` 或 `List State` 不为 `Up` → 跳转步骤 2。
- **根因定位**：
  - Policy 存在，且 `Policy State` 为 `Up`、所有 srlist 的 `List State` 均为 `Up` → 根因：**SRv6 TE Policy状态正常**
    - 修复：无需修复，隧道状态正常。核对告警是否已自动清除；若告警仍未清除，返回本步骤采集到的字段取值供用户判断。
  - 前置检查 1 无回显，即指定 endpoint/color 的 SRv6 TE Policy 不存在 → 根因：**SRv6 TE Policy不存在**
    - 修复（静态配置场景，按需补齐）：
      ```
      segment-routing ipv6
       segment-list <segment-list-name>
        index <index> sid ipv6 <sid-ipv6>
      srv6-te policy <policy-name> endpoint <endpoint-ipv6> color <color-id>
       candidate-path preference <preference>
        segment-list <segment-list-name>
      ```
    - 复检命令：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（标准：可查询到该 Policy 且 `Policy State` 为 `Up`）→ 结束。

### 步骤 2：检查静态配置的 SRv6 TE Policy 完整性

- **CLI命令**：无（基于前置检查 2 回显判定）
- **跳转信息**：
  - 若 `segment-routing ipv6` 下未配置该 endpoint/color 的 SRv6 TE Policy 或未配置 candidate path（判定为 BGP 动态下发场景）→ 跳转步骤 3。
  - 若配置完整（candidate path、所引用的 segment list 及 SID 齐备）→ 跳转步骤 3。
- **根因定位**：
  - 已配置 candidate path，但未引用 segment list，或所引用的 segment list 下无任何 SID 配置 → 根因：**SRv6 TE Policy配置不完整**
    - 修复：
      ```
      segment-routing ipv6
       segment-list <segment-list-name>
        index <index> sid ipv6 <sid-ipv6>
      ```
    - 影响性：配置变更可能引起流量切换，建议在业务低峰期操作。
    - 复检命令：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（标准：`Policy State` 与 `List State` 均为 `Up`，告警清除）→ 结束。

### 步骤 3：检查 BGP 动态下发场景的地址族配置

- **CLI命令**：无（基于前置检查 3 回显判定）
- **跳转信息**：
  - 若 BGP 视图下已配置 `ipv6-family sr-policy` 地址族 → 跳转步骤 4。
- **根因定位**：
  - BGP 视图下未配置 `ipv6-family sr-policy` 地址族 → 根因：**ipv6-family sr-policy地址族未配置**
    - 修复：
      ```
      bgp <as-number>
       ipv6-family sr-policy
        peer <peer-ip> enable
      ```
    - 复检命令：`display bgp sr-policy ipv6 peer`（标准：对应 peer 状态为 `Established`）→ 结束。

### 步骤 4：检查 SRv6 TE Policy 是否被 shutdown

- **CLI命令**：无（基于前置检查 1 回显的 `Policy State` 字段判定）
- **跳转信息**：
  - 若 `Policy State` 不为 `Down (Shutdown)` → 跳转步骤 5。
- **根因定位**：
  - `Policy State` 为 `Down (Shutdown)` → 根因：**SRv6 TE Policy被shutdown**
    - 修复：
      ```
      srv6-te policy <policy-name> endpoint <endpoint-ipv6> color <color-id>
       undo shutdown
      ```
    - 复检命令：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（标准：`Policy State` 为 `Up`，告警清除）→ 结束。

### 步骤 5：检查是否因 BFD Down 导致中断

- **CLI命令**：`display bfd session srv6-segment-list <segment-list-id>`（仅当前置检查 1 回显中 srlist 的 `BFD State` 为 `Down` 时执行）
- **跳转信息**：
  - 若 srlist 的 `BFD State` 不为 `Down` → 跳转步骤 6。
- **根因定位**：
  - srlist 的 `BFD State` 为 `Down`，且 BFD 会话回显状态为 `Down` → 根因：**bfd检测Down**
    - 修复：执行 `tracert srv6-te policy endpoint-ip <endpoint-ipv6> color <color-id>` 定位路径中断点，再根据结果处理对应的物理链路、接口或中间节点转发故障。
    - 复检命令：`display bfd session srv6-segment-list <segment-list-id>`（标准：会话状态为 `Up`）→ 结束。

### 步骤 6：检查是否因故障感知 Down 导致中断

- **CLI命令**：`display srv6-te policy source-sid`（仅当满足下述根因条件时执行）
- **跳转信息**：
  - 若 srlist 的 `List State` 不为 `Down (SID Stack Down)`，或 `Verification State` 不为 `SID Unreachable` → 跳转步骤 7。
- **根因定位**：
  - srlist 的 `List State` 为 `Down (SID Stack Down)` 且 `Verification State` 为 `SID Unreachable`，且回显中查不到 srlist 所含的 SID → 根因：**故障感知检测Down**
    - 修复：根据回显中的 Producer 与 Node 信息，检查对应 ISIS 进程状态与链路状态，恢复 SID 的 IGP 通告与可达性。
    - 复检命令：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（标准：`Verification State` 不再为 `SID Unreachable`，`List State` 为 `Up`）→ 结束。

### 步骤 7：检查 SRv6 TE Policy 是否超限

- **CLI命令**：`display paf | include SPEC_RES_SRV6POLICY_MAX_NUM`（仅当 `Policy State` 为 `Down (Overrun)` 时执行）
- **跳转信息**：
  - 若 `Policy State` 不为 `Down (Overrun)` → 跳转步骤 8。
- **根因定位**：
  - `Policy State` 为 `Down (Overrun)`，且已配置的 Policy 数量达到规格上限 → 根因：**SRv6 TE Policy超限**
    - 修复：无直接修复 CLI。减少设备上的 SRv6 TE Policy 数量（删除不再使用的 Policy 配置），或通知控制器停止下发冗余 Policy。
    - 复检命令：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（标准：`Policy State` 为 `Up`）→ 结束。

### 步骤 8：检查 SRv6 TE Policy srlist 是否超限

- **CLI命令**：`display paf | include SPEC_RES_SRV6POLICY_SEGLIST_GLOBAL_NUM`（仅当 srlist 的 `List State` 为 `Down (Overrun)` 时执行）
- **跳转信息**：
  - 若 srlist 的 `List State` 不为 `Down (Overrun)` → 排查流程执行完毕，按「终止」要求输出。
- **根因定位**：
  - srlist 的 `List State` 为 `Down (Overrun)`，且 Segment List 数量达到规格上限 → 根因：**srlist超限**
    - 修复：无直接修复 CLI。减少设备上的 Segment List 数量，或通知控制器减少下发的候选路径数量。
    - 复检命令：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`（标准：`List State` 为 `Up`，告警清除）→ 结束。

### 终止

步骤 1~8 全部执行完毕但证据不足以判定根因时，**不得臆造根因结论**。此时返回以下信息供用户判断：

- **故障对象**：网元ID/网元名、SRv6 TE Policy 名称、endpoint IPv6 + color ID、涉及的 Segment List ID/名称。
- **采集时间**：各条 CLI 的执行时间戳。
- **已执行检查项**：步骤 1~8 逐条列出所执行的 CLI 命令与结论摘要。
- **关键回显**：`Policy State`、`List State`、`BFD State`、`Verification State` 的实际取值，以及各 CLI 的原始回显片段。

## 根因对照表

| 根因 | 现象 | 修复CLI和方法 |
| --- | --- | --- |
| SRv6 TE Policy状态正常 | `Policy State` 为 `Up`，且所有 srlist 的 `List State` 均为 `Up` | 无需修复。隧道状态正常，核对告警是否已自动清除 |
| SRv6 TE Policy不存在 | 查询该 endpoint/color 的 Policy 无回显 | 静态场景补齐 `segment-routing ipv6` 下的 segment-list 与 srv6-te policy 配置；动态场景确认控制器下发及 BGP 地址族 |
| SRv6 TE Policy配置不完整 | 已配置 candidate path，但未引用 segment list，或 segment list 下无 SID | 在 segment-list 下补齐 SID 栈配置。建议业务低峰期操作 |
| ipv6-family sr-policy地址族未配置 | BGP 配置中无 `ipv6-family sr-policy`，控制器下发的 Policy 无法生效 | `bgp <as-number>` → `ipv6-family sr-policy` → `peer <peer-ip> enable`；复检 `display bgp sr-policy ipv6 peer` 为 `Established` |
| SRv6 TE Policy被shutdown | `Policy State` 为 `Down (Shutdown)` | 在该 Policy 视图下执行 `undo shutdown` |
| bfd检测Down | srlist 的 `BFD State` 为 `Down`，BFD 会话为 Down | `tracert srv6-te policy endpoint-ip <endpoint-ipv6> color <color-id>` 定位断点，处理物理链路/接口/中间节点故障 |
| 故障感知检测Down | srlist `List State` 为 `Down (SID Stack Down)` 且 `Verification State` 为 `SID Unreachable` | `display srv6-te policy source-sid` 查 Producer/Node，恢复 ISIS 邻居、链路或 locator 通告，使 SID 恢复可达 |
| SRv6 TE Policy超限 | `Policy State` 为 `Down (Overrun)`，数量达规格上限 | 无直接修复 CLI；减少冗余 Policy，或升级设备规格 |
| srlist超限 | srlist `List State` 为 `Down (Overrun)`，数量达规格上限 | 无直接修复 CLI；减少冗余 Segment List，或升级设备规格 |
| 未找到根因 | 步骤 1~8 全部执行完毕，各关键字段均未命中上述任一条件 | 不做根因判定，按「终止」要求返回故障对象、采集时间、已执行检查项与原始回显，交由用户研判 |
