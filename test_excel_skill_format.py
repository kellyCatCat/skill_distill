#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 excel_skill_distill_pipeline.check_skill_format。

这个校验器是输出格式约束的唯一执行者——四个小节齐不齐、命令用没用反引号、参数是不是
统一的 `<>`、修复CLI有没有抄样例回显里的具体值、根因对照表有没有漏掉步骤表写明的根因。
它挡的是模型的输出，而蒸馏用的模型接口只在内网可达，在没有内网的机器上跑不了整条
流水线，就没有别的东西能验证它了。所以这里用手写样例覆盖：一份合规的要放过，每类
违规要各自被拦下且报错说得清。

合规样例取自 `excel_cases/sample_skill.md`（格式基准，同时也是 mock 跑用的假回复）。

用法：
  python3 test_excel_skill_format.py
"""
import re
import sys

from excel_skill_distill_pipeline import check_skill_format, parse_sheet

SAMPLE_PATH = "excel_cases/sample_skill.md"

scenario = parse_sheet("excel_cases/排障步骤表.xlsx")[0]
GOOD = open(SAMPLE_PATH, encoding="utf-8").read()


def variant(old, new, count=1):
    assert old in GOOD, f"格式基准里找不到 {old!r}"
    return GOOD.replace(old, new, count)


PRECHECK_CMD = "`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`"
PRECHECK_BGP = "   - CLI 命令：`display current-configuration configuration bgp`"

CASES = [
    ("合规样例", GOOD, ""),

    # ---- 命令与参数 ----
    # 这条命令在步骤6和根因对照表里各出现一次，只去掉一处的反引号不够
    ("命令没用反引号包裹",
     variant("`display srv6-te policy source-sid`", "display srv6-te policy source-sid",
             count=99),
     "没有以行内代码"),
    ("参数用了花括号",
     variant("`display bfd session srv6-segment-list <segment-list-id>`",
             "`display bfd session srv6-segment-list {segment-list-id}`"),
     "不是尖括号形式"),
    ("同一参数两种写法",
     variant(PRECHECK_CMD,
             "`display srv6-te policy endpoint <endpoint-ipv6> color <colorid>`"),
     "多种写法"),
    ("残留表内编号说法",
     variant(PRECHECK_BGP, "   - CLI 命令：执行3号命令行"),
     "表内编号说法"),
    ("漏写了一条命令（前缀与别条相同）",
     variant("SPEC_RES_SRV6POLICY_SEGLIST_GLOBAL_NUM", "SPEC_RES_SRV6POLICY_MAX_NUM",
             count=99),
     "没有以行内代码"),
    ("编造了表里没有的查询命令",
     GOOD + "\n- 补充验证：执行 `display srv6-te policy summary` 确认数量。\n",
     "在步骤表中不存在"),

    # ---- 修复CLI里抄了样例回显的具体值 ----
    ("修复CLI写死了AS号", variant("`bgp <as-number>`", "`bgp 100`"), "写成了具体值"),
    ("修复CLI写死了segment-list名",
     variant("`segment-list <segment-list-name>`", "`segment-list list1`"),
     "写成了具体值"),
    ("子关键字不误判",
     GOOD + "\n```\nbgp route-learning acceleration enable\n```\n", ""),

    # ---- 把问题转出去的兜底措辞 ----
    ("写了转人工的兜底",
     variant("交用户判断", "转人工分析或收集诊断信息升级处理"),
     "把问题转出去"),

    # ---- 四个章节的结构 ----
    ("缺少根因对照表", GOOD[:GOOD.index("# 根因对照表")], "缺少必需的小节"),
    ("前置检查里有内部跳转",
     variant("   - 采集内容：是否存在 `ipv6-family sr-policy` 地址族及其 peer 使能情况。",
             "   - 采集内容：若地址族缺失则跳转步骤 3。"),
     "前置检查必须是线性执行"),
    ("前置检查用了非必填参数",
     variant("   - CLI 命令：" + PRECHECK_CMD,
             "   - CLI 命令：`display srv6-te policy endpoint <endpoint-ipv6> "
             "color <color-id> segment-list <segment-list-id>`"),
     "不可以超出入参列表"),

    # ---- 根因：步骤表写明的根因、正文、对照表三者要对得上 ----
    ("根因对照表漏了一个根因",
     variant("| srlist 超限 | srlist 的 `List State` 为 `Down (Overrun)`", "| xx | yy"),
     "根因对照表漏了"),
    ("根因只在对照表里、正文没判到",
     variant("4. **根因定位**：\n   - srlist 超限\n   - 未找到根因",
             "4. **根因定位**：\n   - 未找到根因"),
     "正文里没有判到"),
    ("排查步骤没有步骤标题",
     re.sub(r"^## 步骤(\d+)：", r"### 第\1项 ", GOOD, flags=re.M),
     "没有找到形如"),
    ("跳转指向不存在的步骤",
     variant("跳转步骤3", "跳转步骤99"),
     "不存在的步骤"),

    # ---- 整体完整性 ----
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
