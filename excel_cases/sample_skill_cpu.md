---
name: cpu-utilization-rising
description: 单板CPU利用率超限。出现CPU占用率持续升高或CPU利用率超限告警时使用，覆盖报文攻击、网管采集、用户上线、路由协议震荡四类冲高源。
---

# 入参列表

| 信息 | 是否必填 | 说明 |
| --- | --- | --- |
| 网元ID | 是 | 网元的resId |
| slot ID | 是 | CPU占用率高的单板槽位号，来自告警 |
| process ID | 否 | 进程号，从前置检查步骤2的进程CPU占用率回显中取，无需人工输入 |

# 前置检查

前置检查按顺序线性执行，仅用于采集后续排查所需的回显信息，不做跳转。

1. **查询单板上各进程的CPU占用率**
   - CLI 命令：`display cpu-usage process slot <slot-id>`
   - 采集内容：CPU占用率前3高的进程名与进程号，重点关注是否有 PROTO2、CFG 进程。

2. **查询单板的CP-CAR上送统计**
   - CLI 命令：`display cpu-defend statistics-all slot <slot-id>`
   - 采集内容：各通道的 Dropped Packets 列与 Index 列，用于判断是否有某类报文上送过多。

3. **查询CPU占用率高的服务类型**
   - CLI 命令：`display cpu-usage service slot <slot-id>`
   - 采集内容：占用率高的服务类型与组件名，重点关注 BRAS、BR_UCM、BR_AAA、BR_RADIUS、BR_DACC、PPPOE、PPP。

# 排查步骤

默认按顺序执行。步骤1直接复用前置检查步骤1已采集的回显，无需重复执行 CLI。

## 步骤1：确定CPU占用率最高的进程

1. **步骤名称**：确定CPU占用率最高的进程
2. **CLI 命令**：`display cpu-usage process slot <slot-id>`（复用前置检查步骤1回显，查看前3高的进程）
3. **跳转信息**：
   - 前3高的进程中有 PROTO2：跳转步骤 3。
   - 前3高的进程中有 CFG：跳转步骤 5。
   - 前3高的进程为其他进程：顺序执行步骤2。
4. **根因定位**：
   - 无

## 步骤2：查看该进程内各组件的CPU占用率

1. **步骤名称**：查看该进程内各组件的CPU占用率
2. **CLI 命令**：`display cpu-usage process <process-id>`（进程号取自步骤1回显）
3. **跳转信息**：
   - 取到占用率前3高的组件后，跳转步骤 11 继续确认是否为用户上线导致。
4. **根因定位**：
   - 路由协议震荡

## 步骤3：确认是否为单板报文上送过多

1. **步骤名称**：确认是否为单板报文上送过多
2. **CLI 命令**：
   - `display cpu-usage process slot <slot-id>`（复用前置检查步骤1回显，确认 PROTO2 进程占用率高）
   - `display cpu-usage process <process-id>`（查看该进程内组件占用率，确认 CACHE 组件是否占用率高）
3. **跳转信息**：
   - PROTO2 进程与 CACHE 组件占用率均高：顺序执行步骤4。
   - 不满足：跳转步骤 11。
4. **根因定位**：
   - 无

## 步骤4：查看CP-CAR上送计数确认报文类型

1. **步骤名称**：查看CP-CAR上送计数确认报文类型
2. **CLI 命令**：`display cpu-defend statistics-all slot <slot-id>`（复用前置检查步骤2回显，连续多次查看 Dropped Packets 列与 Index 列）
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

## 步骤6：确认网管通过SNMP/NETCONF查询了哪些业务

1. **步骤名称**：确认网管通过SNMP/NETCONF查询了哪些业务
2. **CLI 命令**：`display cmf-info luascript history process <process-id>`（CPU占用率高时多次执行，回显次数多的即网管采集的内容）
3. **跳转信息**：
   - 采集到网管执行的业务脚本：顺序执行步骤7。
   - 未采集到业务脚本：跳转步骤 11。
4. **根因定位**：
   - 无

## 步骤7：采集耗时长的业务脚本

1. **步骤名称**：采集耗时长的业务脚本
2. **CLI 命令**：`display cmf-info luascript longtime process <process-id>`
3. **跳转信息**：
   - 顺序执行步骤8。
4. **根因定位**：
   - 无

## 步骤8：查看网管采集行为统计信息

1. **步骤名称**：查看网管采集行为统计信息
2. **CLI 命令**：`display snmp-agent statistics nms`（查看哪些SNMP网管执行了采集行为）
3. **跳转信息**：
   - 存在采集行为频繁的网管：顺序执行步骤9。
   - 无SNMP网管采集行为：跳转步骤 11。
4. **根因定位**：
   - 无

## 步骤9：查看网管访问MIB节点的统计信息

1. **步骤名称**：查看网管访问MIB节点的统计信息
2. **CLI 命令**：`display snmp-agent statistics mib`（对比昨天与今天网管访问设备MIB节点的统计信息）
3. **跳转信息**：
   - 顺序执行步骤10。
4. **根因定位**：
   - 无

## 步骤10：查看MIB节点请求超时的统计信息

1. **步骤名称**：查看MIB节点请求超时的统计信息
2. **CLI 命令**：`display snmp-agent statistics mib timeout`（记录超时节点的OID）
3. **跳转信息**：
   - 存在超时的MIB节点：定位根因，结束排查。
   - 无超时节点：顺序执行步骤11。
4. **根因定位**：
   - 管理面同步数据导致CPU冲高

## 步骤11：确认是否有NETCONF网管在执行同步

1. **步骤名称**：确认是否有NETCONF网管在执行同步
2. **CLI 命令**：`display users`（查看登录用户的IP地址；NETCONF占用NCA通道，与普通VTY通道有区别）
3. **跳转信息**：
   - 存在NETCONF网管正在执行全量或增量同步：定位根因，结束排查。
   - 无同步行为：顺序执行步骤12。
4. **根因定位**：
   - 管理面同步数据导致CPU冲高

## 步骤12：查看CPU占用率高的服务类型

1. **步骤名称**：查看CPU占用率高的服务类型
2. **CLI 命令**：`display cpu-usage service slot <slot-id>`（复用前置检查步骤3回显）
3. **跳转信息**：
   - 主控板出现 BRAS、BR_UCM、BR_AAA、BR_RADIUS、BR_DACC，或接口板出现 BRAS、PPPOE、PPP 服务类型占用率高：顺序执行步骤13。
   - 以上服务类型均不符合：定位根因，结束排查。
4. **根因定位**：
   - 路由协议震荡

## 步骤13：查看用户上线和下线失败的TOP原因

1. **步骤名称**：查看用户上线和下线失败的TOP原因
2. **CLI 命令**：
   - `display aaa offline-record statistics`（查看2天内每10分钟数量最多的5条下线原因）
   - `display aaa online-fail-record statistics`（查看2天内每10分钟数量最多的5条上线失败原因）
3. **跳转信息**：
   - 取到上线失败或下线原因的TOP5：定位根因，结束排查。
   - 两条命令都取不到下线原因：顺序执行步骤14。
4. **根因定位**：
   - 业务负载高导致CPU冲高

## 步骤14：从UCM模块的下线原因统计确认TOP原因

1. **步骤名称**：从UCM模块的下线原因统计确认TOP原因
2. **CLI 命令**：
   - `reset ucm statistics offline-reason`（先清除UCM的统计信息）
   - `display ucm statistics offline-reason`（每间隔10分钟执行一次，按原因数量确认TOP原因）
3. **跳转信息**：
   - 统计到TOP下线原因：定位根因，结束排查。
   - 全部步骤走完仍未命中任何故障特征：判定"未找到根因"，输出已执行的全部检查步骤及结果摘要，结束排查。
4. **根因定位**：
   - 业务负载高导致CPU冲高

# 根因对照表

| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |
| --- | --- | --- | --- |
| 报文攻击导致CPU冲高 | CP-CAR上送计数中某类报文持续增长，攻击溯源回显给出攻击报文类型与来源接口 | 排查报文来源，确认报文上送过多的原因并降低报文上送数量；可临时部署白名单或调整CAR动作降低CPU占用率。表里未给出具体配置命令，不展开为CLI | `display cpu-usage process slot <slot-id>` |
| 管理面同步数据导致CPU冲高 | 网管存在SNMP/NETCONF采集或同步行为，MIB节点请求超时，或NCA通道有登录用户 | 无直接修复CLI，只能定位：按超时节点的OID找对应业务模块分析耗时原因，并与网管侧确认采集频度 | `display snmp-agent statistics mib timeout` |
| 业务负载高导致CPU冲高 | 服务类型中BRAS/BR_UCM/BR_AAA/BR_RADIUS/BR_DACC或PPPOE/PPP占用率高，且上线失败与下线原因统计集中 | 无直接修复CLI，只能定位：按下线与上线失败的TOP原因确认是否为用户大量上线导致 | `display cpu-usage service slot <slot-id>` |
| 路由协议震荡 | 占用率高的进程与组件均不属于上述场景，服务类型也不符合BRAS类特征 | 无直接修复CLI，只能定位：按占用率前3高的组件确认对应协议的震荡情况 | `display cpu-usage process <process-id>` |
| 未找到根因 | 全部步骤走完，各项判据均不命中 | 输出已执行的全部检查步骤及结果摘要 | - |
