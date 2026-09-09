---
name: cpu-utilization-rising-alarm
description: CPU利用率超限定位。出现 hwCPUUtilizationRisingAlarm 告警或单板CPU占用率持续升高时使用，覆盖报文攻击、NETCONF查询/同步、SNMP MIB操作、业务脚本超时、路由协议震荡五类冲高源。
---

# 入参列表

| 信息 | 是否必填 | 说明 |
| --- | --- | --- |
| 网元ID | 是 | 网元的resId |
| slot ID | 是 | CPU占用率高的单板槽位号，来自告警 |
| process ID | 否 | 进程号，从前置检查步骤1的进程CPU占用率回显中取，无需人工输入 |
| mib ID | 否 | MIB节点标识，从步骤7的超时节点 VB[0] 中取，无需人工输入 |

# 前置检查

前置检查按顺序线性执行，仅用于采集后续排查所需的回显信息，不做跳转。

1. **查询单板上各进程的CPU占用率**
   - CLI 命令：`display cpu-usage process slot <slot-id>`
   - 采集内容：CPU占用率前3高的进程名与进程号，重点关注是否有 PROTO2、CFG 进程。

2. **查询单板的CP-CAR上送统计**
   - CLI 命令：`display cpu-defend statistics-all slot <slot-id>`
   - 采集内容：各通道的上送与丢包计数，用于判断是否有某类报文上送过多。

# 排查步骤

默认按顺序执行。步骤1直接复用前置检查步骤1已采集的回显，无需重复执行 CLI。

## 步骤1：确定CPU占用率最高的进程

1. **步骤名称**：确定CPU占用率最高的进程
2. **CLI 命令**：`display cpu-usage process slot <slot-id>`（复用前置检查步骤1回显，查看前3高的进程）
3. **跳转信息**：
   - 前3高的进程中有 PROTO2：跳转步骤 3。
   - 前3高的进程中有 CFG：跳转步骤 6。
   - 前3高的进程为其他进程：顺序执行步骤2。
4. **根因定位**：
   - 无

## 步骤2：查看该进程内各组件的CPU占用率

1. **步骤名称**：查看该进程内各组件的CPU占用率
2. **CLI 命令**：`display cpu-usage process <process-id>`（进程号取自步骤1回显）
3. **跳转信息**：
   - 取到占用率前3高的组件，且不属于后续步骤的场景：定位根因，结束排查。
4. **根因定位**：
   - 路由协议震荡

## 步骤3：确认是否为单板报文上送过多

1. **步骤名称**：确认是否为单板报文上送过多
2. **CLI 命令**：
   - `display cpu-usage process slot <slot-id>`（复用前置检查步骤1回显，确认 PROTO2 进程占用率高）
   - `display cpu-usage process <process-id>`（查看该进程内组件占用率，确认 CACHE 组件是否占用率高）
3. **跳转信息**：
   - PROTO2 进程与 CACHE 组件占用率均高：顺序执行步骤4。
   - 不满足：跳转步骤 6。
4. **根因定位**：
   - 无

## 步骤4：查看CP-CAR上送计数确认报文类型

1. **步骤名称**：查看CP-CAR上送计数确认报文类型
2. **CLI 命令**：`display cpu-defend statistics-all slot <slot-id>`（复用前置检查步骤2回显，连续多次查看上送计数）
3. **跳转信息**：
   - 存在明显上送过多的报文通道：顺序执行步骤5确认攻击源。
   - 各通道上送计数均正常：跳转步骤 6。
4. **根因定位**：
   - 无

## 步骤5：查看单板攻击溯源的详细信息

1. **步骤名称**：查看单板攻击溯源的详细信息
2. **CLI 命令**：`display attack-source-trace slot <slot-id> verbose`（确认攻击报文类型、接口与 VLAN 信息）
3. **跳转信息**：
   - 溯源到攻击报文的来源接口与报文类型：定位根因，结束排查。
   - 未溯源到攻击源：跳转步骤 6。
4. **根因定位**：
   - 报文攻击导致CPU冲高

## 步骤6：确认NETCONF查询或同步操作占用CPU的业务模块

1. **步骤名称**：确认NETCONF查询或同步操作占用CPU的业务模块
2. **CLI 命令**：
   - `display netconf data-flow statistics verbose`（查看 NETCONF 查询操作，最近的记录在最上方）
   - `display netconf sync-full statistics verbose`（查看 NETCONF 全量同步操作）
3. **跳转信息**：
   - 查询操作记录的 `Operation Received Timestamp` 与 `Operation completed Timestamp` 区间覆盖CPU冲高时间点：按 `Processing Time` 与 `Record Number` 取耗时和记录数前三的 YANG 模块，定位根因，结束排查。
   - 全量同步记录的时间区间覆盖CPU冲高时间点：按 `CmfTime` 与 `RecordNumber` 取前三的 YANG 模块，定位根因，结束排查。
   - 两类记录的时间区间都不覆盖CPU冲高时间点：顺序执行步骤7。
4. **根因定位**：
   - NETCONF查询或同步操作占用CPU

## 步骤7：确认SNMP MIB操作占用CPU的MIB节点

1. **步骤名称**：确认SNMP MIB操作占用CPU的MIB节点
2. **CLI 命令**：
   - `display snmp-agent statistics mib`（记录 `Total MIB number` 与各节点的 SET/GET/GETNEXT/GETBULK 计数，间隔30s后再执行一次做对比）
   - `display snmp-agent statistics mib timeout`（查看请求超时的节点，默认处理超过5s才记录）
   - `display snmp-agent mib node <mib-id>`（仅当上一条取到超时节点 VB[0] 时执行，用于取该节点的 name）
3. **跳转信息**：
   - 两次查询的 `Total MIB number` 未发生变更：说明不是SNMP操作导致，顺序执行步骤8。
   - `Total MIB number` 发生变更：取两次计数差异前三的 MIB 节点，定位根因，结束排查。
4. **根因定位**：
   - SNMP MIB操作占用CPU

## 步骤8：采集耗时长的业务脚本

1. **步骤名称**：采集耗时长的业务脚本
2. **CLI 命令**：`display cmf-info luascript longtime process <process-id>`（按 `StartRunTime`、`FinishRunTime` 筛选覆盖CPU冲高时间点的记录）
3. **跳转信息**：
   - 存在 `StartRunTime` 与 `FinishRunTime` 覆盖CPU冲高时间点的脚本：按 `SptFileName` 取脚本名称，定位根因，结束排查。
   - 全部步骤走完仍未命中任何故障特征：判定"未找到根因"，输出已执行的全部检查步骤及结果摘要，结束排查。
4. **根因定位**：
   - 业务脚本执行超时

# 根因对照表

| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |
| --- | --- | --- | --- |
| 报文攻击导致CPU冲高 | CP-CAR某类报文上送计数持续增长，攻击溯源回显给出攻击报文类型与来源接口 | 排查报文来源并降低报文上送数量；应用防攻击策略：`system-view` → `slot <slot-id>` → `cpu-defend-policy 8`。影响性：修改的是单板防攻击策略，建议在业务低峰执行 | `display cpu-usage process slot <slot-id>` |
| NETCONF查询或同步操作占用CPU | 查询或全量同步记录的时间区间覆盖CPU冲高时间点，`Processing Time`／`CmfTime` 与记录数集中在少数 YANG 模块 | 无直接修复CLI，只能定位：按会话记录的 `Host Identifier` 找到发起操作的控制器，在控制器侧去勾选耗时和记录数前三的 YANG 模块后再执行查询或全量同步 | `display netconf data-flow statistics verbose` |
| SNMP MIB操作占用CPU | 两次 `display snmp-agent statistics mib` 的 `Total MIB number` 发生变更，且少数 MIB 节点的操作计数差异明显 | 无直接修复CLI，只能定位：按节点的 SourceIP／Port／VPNId 确认发起操作的控制器，在控制器侧去勾选这些 MIB 节点 | `display snmp-agent statistics mib timeout` |
| 业务脚本执行超时 | 存在 `StartRunTime` 与 `FinishRunTime` 覆盖CPU冲高时间点的业务脚本 | 无直接修复CLI，只能定位：按 `SptFileName` 找到对应业务模块确认脚本耗时原因 | `display cmf-info luascript longtime process <process-id>` |
| 路由协议震荡 | 占用率高的进程与组件均不属于上述场景 | 无直接修复CLI，只能定位：按占用率前3高的组件确认对应协议的震荡情况 | `display cpu-usage process <process-id>` |
| 未找到根因 | 全部步骤走完，各项判据均不命中 | 输出已执行的全部检查步骤及结果摘要 | - |
