#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 excel_skill_distill_pipeline.check_skill_format。

这个校验器是输出格式约束（命令用反引号、参数统一 <>、分支逐条展开、跳转指向真实
步骤）的唯一执行者，而它挡的是模型的输出——蒸馏用的模型接口只在内网可达，在没有
内网的机器上跑不了整条流水线，就没有别的东西能验证它了。所以这里用手写样例覆盖：
一份合规的要放过，每类违规要各自被拦下且报错说得清。

下面的 GOOD 是**手写的格式基准，不是流水线产出的skill**，只用来测校验器。

用法：
  python3 test_excel_skill_format.py
"""
import re
import sys

from excel_skill_distill_pipeline import check_skill_format, parse_sheet

scenario = parse_sheet("excel_cases/排障步骤表.xlsx")[0]

GOOD = """---
name: srv6-te-policy-down
description: SRv6 TE Policy down告警的逐步排查与修复指引。
---

# SRv6 TE Policy Down 排障指南

触发告警：SRv6 TE Policy down

## 排查步骤

1. **检查隧道状态**
   - 执行 `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>` 查询指定 endpoint/color 的 SRv6 TE Policy。
     - 若该 SRv6 TE Policy 不存在，判定根因为"SRv6 TE Policy 不存在"，结束排查。
     - 若 `Policy State` 不为 `Up`，继续执行步骤2。
     - 若 `Policy State` 为 `Up` 但 Segment-List 的 `List State` 不为 `Up`，继续执行步骤2。
     - 若 `Policy State` 与 `List State` 均为 `Up`，判定隧道状态正常，结束排查。
   - 本命令的回显是步骤4至步骤8的判据来源，保留回显，后续无需重复执行。

2. **检查静态配置的 SRv6 TE Policy 是否完整**
   - 执行 `display current-configuration configuration segment-routing-ipv6` 查看 segment-routing ipv6 下的配置。
     - 若未配置 SRv6 TE Policy 或 candidate path，继续执行步骤3。
     - 若已配置 candidate path 但未引用 segment-list、或 segment-list 下无 SID 配置，判定根因为"SRv6 TE Policy 配置不完整"，按方案一修复，结束排查。

3. **检查 BGP 动态下发场景的地址族配置**
   - 执行 `display current-configuration configuration bgp` 查看 BGP 下是否配置 `ipv6-family sr-policy` 地址族。
     - 若未配置该地址族，判定根因为"ipv6-family sr-policy 地址族未配置"，按方案二修复，结束排查。
     - 若已配置，继续执行步骤4。

4. **检查 SRv6 TE Policy 是否被 shutdown**
   - 读取步骤1回显中的 `Policy State` 字段。
     - 若 `Policy State` 为 `Down (Shutdown)`，判定根因为"SRv6 TE Policy 被 shutdown"，按方案三修复，结束排查。
     - 否则继续执行步骤5。

5. **检查是否 BFD Down 导致中断**
   - 读取步骤1回显中 Segment-List 的 `BFD State` 字段。
     - 若 `BFD State` 不为 `Down`，继续执行步骤6。
     - 若 `BFD State` 为 `Down`，执行 `display bfd session srv6-segment-list <segment-list-id>` 确认 BFD 会话状态，判定根因为"BFD 检测 Down"，按方案四修复，结束排查。

6. **检查是否故障感知 Down 导致中断**
   - 读取步骤1回显中 Segment-List 的 `List State` 与 `Verification State` 字段。
     - 若 `List State` 不为 `Down (SID Stack Down)`，或 `Verification State` 不为 `SID Unreachable`，继续执行步骤7。
     - 若两者同时成立，执行 `display srv6-te policy source-sid` 查看 ISIS 拓扑中是否存在 Segment-List 中的 SID，判定根因为"故障感知检测 Down"，结束排查。

7. **检查 SRv6 TE Policy 是否超限**
   - 读取步骤1回显中的 `Policy State` 字段。
     - 若 `Policy State` 不为 `Down (Overrun)`，继续执行步骤8。
     - 若 `Policy State` 为 `Down (Overrun)`，执行 `display paf | include SPEC_RES_SRV6POLICY_MAX_NUM` 查看设备支持的规格，判定根因为"SRv6 TE Policy 超限"，按方案五修复，结束排查。

8. **检查 Segment-List 是否超限**
   - 读取步骤1回显中 Segment-List 的 `List State` 字段。
     - 若 `List State` 不为 `Down (Overrun)`，说明以上根因均不成立，输出"未找到根因"并列出已执行的检查项。
     - 若 `List State` 为 `Down (Overrun)`，执行 `display paf | include SPEC_RES_SRV6POLICY_SEGLIST_GLOBAL_NUM` 查看设备支持的规格，判定根因为"Segment-List 超限"，按方案六修复，结束排查。

## 诊断结论输出要求

定位到根因时输出：故障对象（网元名 + SRv6 TE Policy 名/endpoint/color）、根因类型、原因、修复建议。
未定位到根因时输出"未找到根因"并列出已执行的检查项与结论。

## 修复方案输出要求

- 方案三：SRv6 TE Policy 被 shutdown。修复对象为该 Policy，CLI 序列 `undo shutdown`。
- 方案二：地址族未配置。CLI 序列 `ipv6-family sr-policy`，验证执行 `display bgp sr-policy ipv6 peer`，期望 peer 状态为 Established。
"""


def variant(old, new):
    assert old in GOOD, f"样例里找不到 {old!r}"
    return GOOD.replace(old, new, 1)


CASES = [
    ("合规样例", GOOD, ""),
    ("命令没用反引号包裹",
     variant("`display srv6-te policy source-sid`", "display srv6-te policy source-sid"),
     "没有以行内代码"),
    ("参数用了花括号",
     variant("`display bfd session srv6-segment-list <segment-list-id>`",
             "`display bfd session srv6-segment-list {segment-list-id}`"),
     "不是尖括号形式"),
    # 同一参数两种写法：必须让两种拼法同时存在才构成冲突，
    # 所以是"再加一处用旧拼法的命令"，而不是把唯一那处替换掉
    ("同一参数两种写法",
     variant("   - 本命令的回显是步骤4至步骤8的判据来源，保留回显，后续无需重复执行。",
             "   - 也可执行 `display srv6-te policy endpoint <endpoint-ipv6> "
             "color <colorid>` 复查。"),
     "多种写法"),
    ("残留表内编号说法",
     variant("执行 `display srv6-te policy source-sid`", "执行6号命令行"),
     "表内编号说法"),
    ("跳转指向不存在的步骤",
     variant("继续执行步骤8。", "继续执行步骤99。"),
     "不存在的步骤"),
    ("漏写了一条命令（前两词与别条相同）",
     variant("`display paf | include SPEC_RES_SRV6POLICY_SEGLIST_GLOBAL_NUM`",
             "`display paf | include SPEC_RES_SRV6POLICY_MAX_NUM`"),
     "没有以行内代码"),
    # 要触发"没找到步骤"必须把全部编号步骤都改掉：只改第一个的话，
    # 剩下的步骤仍在，先撞上的是"步骤1指向不存在的步骤"——那个报错也是对的
    ("没有加粗编号步骤",
     re.sub(r"^(\d+)\. \*\*(.+?)\*\*", r"### \2", GOOD, flags=re.M),
     "不符合输出格式要求"),
    ("缺frontmatter", GOOD.split("---\n", 2)[2], "缺少frontmatter"),
    ("围栏未闭合", GOOD + "\n```bash\ndisplay xxx\n", "围栏未闭合"),
]

def run() -> int:
    failures = 0
    for name, content, expect in CASES:
        got = check_skill_format(content, scenario)
        ok = (got == "") if expect == "" else (expect in got)
        if not ok:
            failures += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if got:
            print(f"        → {got}")
        if not ok:
            print(f"        期望包含: {expect!r}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} 通过")
    return failures


# GOOD 同时被 mock 跑用作假的模型回复，所以这个模块要能被 import 而不自己跑起来
if __name__ == "__main__":
    sys.exit(1 if run() else 0)
