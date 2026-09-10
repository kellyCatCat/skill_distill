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
import copy
import re
import sys

from excel_skill_distill_pipeline import (check_commands_from_source,
                                          check_hardcoded_operands,
                                          check_skill_format,
                                          check_unknown_commands,
                                          collect_parameters,
                                          normalize_root_causes, parse_rag_index,
                                          parse_sheet, step_commands)

SAMPLE_PATH = "excel_cases/sample_skill.md"

scenario = parse_sheet("excel_cases/排障步骤表.xlsx")[0]
GOOD = open(SAMPLE_PATH, encoding="utf-8").read()

# 根因清单平时由 distill_root_causes 读懂表意抽出来（要一次模型调用），这里直接
# 塞进去：本文件不联网，而且校验器该怎么用这份清单，本来就该拿一份固定的清单来验。
# 内容照着 excel_cases/排障步骤表.xlsx 的"步骤详细描述"写。
scenario["root_causes"] = [
    {"step": "1", "row": 2, "text": "SRv6 TE Policy {endpoint/color}不存在", "normal": False},
    {"step": "1", "row": 2, "text": "SRv6 TE Policy {endpoint/color}状态正常", "normal": True},
    {"step": "2", "row": 3, "text": "SRv6 TE Policy配置不完整", "normal": False},
    {"step": "3", "row": 4, "text": "ipv6-family sr-policy地址族未配置", "normal": False},
    {"step": "4", "row": 5, "text": "SRv6 TE Policy被shutdown", "normal": False},
    {"step": "5", "row": 6, "text": "bfd检测Down", "normal": False},
    {"step": "6", "row": 7, "text": "故障感知检测Down", "normal": False},
    {"step": "7", "row": 8, "text": "SRv6 TE Policy超限", "normal": False},
    {"step": "8", "row": 9, "text": "srlist超限", "normal": False},
]

# 第二张基准表：列不一样（没有排障目标/回显/修复建议/影响性/修复验证），参数在表里
# 是裸词或 `[ ]` 圈着的，还有一条命令只出现在「步骤详细描述」里。整套解析和校验
# 都不能假设"表长得跟第一张一样"。
CPU_SAMPLE_PATH = "excel_cases/sample_skill_cpu.md"
CPU_SCENARIO = parse_sheet("excel_cases/CPU利用率超限步骤表.xlsx")[0]
# 这张表的结论写在散文里（"认为是报文攻击导致CPU冲高"），没有统一句式——正则捞不着，
# 语义抽取能。清单同样是照着表手写的。
CPU_SCENARIO["root_causes"] = [
    {"step": 2, "row": 3, "text": "路由协议震荡", "normal": False},
    {"step": 5, "row": 6, "text": "报文攻击导致CPU冲高", "normal": False},
    {"step": 11, "row": 12, "text": "管理面同步数据导致CPU冲高", "normal": False},
    {"step": 14, "row": 15, "text": "业务负载高导致CPU冲高", "normal": False},
]
CPU_GOOD = open(CPU_SAMPLE_PATH, encoding="utf-8").read()


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
    ("修复CLI写死了AS号",
     GOOD + "\n```\nbgp 100\n```\n", "写成了具体值"),
    ("修复CLI写死了segment-list名",
     GOOD + "\n```\nundo segment-list list1\n```\n", "写成了具体值"),
    # 表里只给"BGP视图下配置ipv6-family sr-policy"一句描述，模型展开成CLI时
    # 会编出表里没有的命令
    ("编造的修复CLI",
     variant("BGP 视图下配置 `ipv6-family sr-policy`",
             "在 BGP 视图下启用地址族：<br>`bgp <as-number>`<br>"
             "`ipv6-family sr-policy`<br>`peer <peer-ip> enable`"),
     "在步骤表里没有出现过"),
    # 从回显里抄配置同样要拦：这几行只在回显列出现过，不是命令来源
    ("从回显抄来的配置行",
     GOOD + "\n```\nbgp route-learning acceleration enable\n```\n",
     "在步骤表里没有出现过"),
    # 命令本身在表里，但参数没申报——用户无处填，agent 也拿不到
    ("命令在表里但参数没申报",
     variant("`display bfd session srv6-segment-list <segment-list-id>`",
             "`display bfd session srv6-segment-list <bfd-session-id>`", count=99),
     "入参列表里没有对应行"),

    # ---- 把问题转出去的兜底措辞 ----
    ("写了转人工的兜底",
     variant("输出已执行的全部检查步骤及结果摘要",
             "输出已执行的全部检查步骤及结果摘要，转人工分析或收集诊断信息升级处理"),
     "把问题转出去"),

    # ---- 四个章节的结构 ----
    ("缺少根因对照表", GOOD[:GOOD.index("# 根因对照表")], "缺少必需的小节"),
    ("前置检查里有内部跳转",
     variant("   - 采集内容：是否存在 `ipv6-family sr-policy` 地址族及其 peer 使能情况。",
             "   - 采集内容：若地址族缺失则跳转步骤 3。"),
     "前置检查必须是线性执行"),
    # 换成一条表里真有、但参数是选填的命令：前置检查只能用必填项。参数已经申报、
    # 只是填了「否」时，报错要说"把这条命令挪出前置检查"，而不是"入参列表里没有
    # 对应行"——列表里明明有那一行，那样说模型改不动
    ("前置检查用了非必填参数",
     variant(PRECHECK_BGP,
             "   - CLI 命令：`display bfd session srv6-segment-list <segment-list-id>`"),
     "把这条命令从前置检查挪到"),

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


# 步骤表里的命令什么关键字开头都有（ospf / isis / mpls / vlan…），而这张基准表
# 恰好只有 display 这一类。曾经"像不像命令"是靠一张关键字白名单判的，白名单外的
# 命令即使按要求写成了行内代码也永远匹配不上，模型三次重试全废且无从改起。
# 这两例专门守住这条：换个关键字，写对了要放过、没用反引号仍要拦。
OTHER_KEYWORD_SCENARIO = copy.deepcopy(scenario)
OTHER_KEYWORD_SCENARIO["steps"][0]["command"] += "\nospf <process-id>"


def with_ospf(command_text: str) -> str:
    """把一条 ospf 命令写进正文，并把它的参数补进入参列表。"""
    content = GOOD.replace(
        "# 根因对照表", f"补充：执行 {command_text} 进入视图。\n\n# 根因对照表", 1)
    return content.replace(
        "| policy 名称 |", "| process ID | 是 | OSPF 进程号 |\n| policy 名称 |", 1)


# 表里的命令带 `[ slot slot-id ]` 这种"可选参数"记号时，正文要么省掉那一段、
# 要么展开成 `slot <slot-id>`——两种写法都得放过。曾经字面量算到方括号里头，
# 只有原样照抄方括号才通得过，而照抄的方括号 agent 敲不了，模型于是无解。
OPTIONAL_ARG_SCENARIO = copy.deepcopy(scenario)
OPTIONAL_ARG_SCENARIO["steps"][0]["command"] += "\ndisplay cpu-usage process [ slot slot-id ]"


def with_cpu(command_text: str, declare_slot: bool = False) -> str:
    """把一条带可选参数的命令写进正文，需要时把 slot 参数补进入参列表。"""
    content = GOOD.replace(
        "# 根因对照表", f"补充：执行 {command_text} 查看进程占用。\n\n# 根因对照表", 1)
    if declare_slot:
        content = content.replace(
            "| policy 名称 |", "| slot ID | 否 | 单板槽位号，缺省查主控板 |\n| policy 名称 |", 1)
    return content


# 表里直接把参数名写成裸词（`display cpu-usage process process-id`，没有尖括号）
# 也是常见写法，正文里那一段会写成 `<process-id>`。逐字比就只有原样照抄才能通过，
# 而照抄的裸参数名 agent 会当成关键字敲进去。
BARE_PARAM_SCENARIO = copy.deepcopy(scenario)
BARE_PARAM_SCENARIO["steps"][0]["command"] += "\ndisplay cpu-usage process process-id"


def with_process(command_text: str, declare: bool = True) -> str:
    """把一条带裸参数名的命令写进正文，并把该参数补进入参列表。"""
    content = GOOD.replace(
        "# 根因对照表", f"补充：执行 {command_text} 查看进程占用。\n\n# 根因对照表", 1)
    if declare:
        content = content.replace(
            "| policy 名称 |", "| process ID | 是 | 进程号 |\n| policy 名称 |", 1)
    return content


# 第三张基准表：11+1 列的完整形态，但「排障目标」每行重填而不是合并、表尾多一列
# 「R23.0是否支持」、ragIndex 那格是带编号的两条用途、修复用的 CLI 填在了
# 「修复建议影响性」那格里。见 excel_cases/build_cpu_alarm_sheet.py。
CPU_ALARM_PATH = "excel_cases/CPU利用率超限定位步骤表.xlsx"
CPU_ALARM_SCENARIOS = parse_sheet(CPU_ALARM_PATH)
CPU_ALARM_SCENARIO = CPU_ALARM_SCENARIOS[0]
CPU_ALARM_SCENARIO["root_causes"] = [
    {"step": "2", "row": 3, "text": "路由协议震荡", "normal": False},
    {"step": "5", "row": 6, "text": "报文攻击导致CPU冲高", "normal": False},
]
CPU_ALARM_GOOD = open("excel_cases/sample_skill_cpu_alarm.md", encoding="utf-8").read()


def scenario_shape(scenarios: list) -> list:
    """[场景数, 每个场景的步骤数]，用来验分块切对了没有。"""
    return [len(scenarios)] + [len(s["steps"]) for s in scenarios]


# 单独直查的用例：这些分支在 check_skill_format 里会被更早的检查抢先命中，
# 但分支本身的行为仍要验（否则改动它时没人发现）。
DIRECT_CASES = [
    ("非display开头的命令写对了要放过", check_skill_format,
     (with_ospf("`ospf <process-id>`"), OTHER_KEYWORD_SCENARIO), ""),
    ("非display开头的命令没用反引号仍要拦", check_skill_format,
     (with_ospf("ospf <process-id>"), OTHER_KEYWORD_SCENARIO), "没有以行内代码"),
    ("可选参数整段省掉要放过", check_skill_format,
     (with_cpu("`display cpu-usage process`"), OPTIONAL_ARG_SCENARIO), ""),
    ("可选参数展开成尖括号要放过", check_skill_format,
     (with_cpu("`display cpu-usage process slot <slot-id>`", declare_slot=True),
      OPTIONAL_ARG_SCENARIO), ""),
    ("照抄方括号要被拦下", check_skill_format,
     (with_cpu("`display cpu-usage process [ slot slot-id ]`"),
      OPTIONAL_ARG_SCENARIO), "语法记号"),
    ("带可选参数的命令没用反引号仍要拦", check_skill_format,
     (with_cpu("display cpu-usage process"), OPTIONAL_ARG_SCENARIO), "没有以行内代码"),
    ("裸参数名写成尖括号要放过", check_skill_format,
     (with_process("`display cpu-usage process <process-id>`"),
      BARE_PARAM_SCENARIO), ""),
    # 关键字漏了一个词就是另一条命令，仍要拦；报错里要指出正文里最接近的那段
    ("裸参数名的命令漏了关键字要拦", check_skill_format,
     (with_process("`display cpu-usage <process-id>`"),
      BARE_PARAM_SCENARIO), "最接近的是"),
    ("裸参数名的命令整条没写要拦", check_skill_format,
     (with_process("`display cpu-usage`", declare=False),
      BARE_PARAM_SCENARIO), "没有以行内代码"),

    # ---- 第二张基准表（列不一样，见文件开头） ----
    ("另一张表的合规样例", check_skill_format, (CPU_GOOD, CPU_SCENARIO), ""),
    # `display users` 只在「步骤详细描述」里出现（那一步的「命令行」格写的是 NA），
    # 它也是表给的命令，不该被当成模型自己编的
    ("只写在详细描述里的命令要放过", check_unknown_commands,
     ("正文：`display users`", CPU_SCENARIO), ""),
    ("详细描述里也没有的命令仍要拦", check_unknown_commands,
     ("正文：`display sessions all`", CPU_SCENARIO), "在步骤表中不存在"),
    ("另一张表漏写一条命令要拦", check_skill_format,
     (CPU_GOOD.replace("`display snmp-agent statistics mib timeout`", "该命令", 99),
      CPU_SCENARIO), "没有以行内代码"),
    ("另一张表编造命令要拦", check_skill_format,
     (CPU_GOOD + "\n- 补充：执行 `display cpu-usage summary` 确认。\n",
      CPU_SCENARIO), "在步骤表中不存在"),
    # 根因清单是语义抽出来的，抽完照样要校验对照表有没有漏——这张表的结论写在
    # 散文里（"认为是报文攻击导致CPU冲高"），正则捞不到，从前等于没校验
    ("另一张表漏了一个根因要拦", check_skill_format,
     (CPU_GOOD.replace("| 报文攻击导致CPU冲高 |", "| 其它原因 |", 1), CPU_SCENARIO),
      "根因对照表漏了"),
    # 表里 `car-index` 只写在「步骤详细描述」里。模型改了名字（写成 `<car-id>`）时，
    # 该说的是"改回表里的写法"——说"这条CLI是自己编的、删掉它"会让它把对的命令删了
    ("参数名被改过要指出表里的写法", check_skill_format,
     (CPU_GOOD.replace("`display attack-source-trace slot <slot-id> verbose`",
                       "`display attack-source-trace slot <slot-id> verbose "
                       "car-index <car-id>`", 1),
      CPU_SCENARIO), "步骤表里这个参数写作 `<car-index>`"),
    # 报错里要列出表里可用的命令，否则模型改一版又编一条别的
    ("编造命令的报错要列出可用命令", check_unknown_commands,
     ("正文：`display logbuffer`", CPU_SCENARIO), "本表可用的命令只有"),
    # 回显里的设备提示符 `<HUAWEI>` 长得像参数，但它不是——写进"入参列表必须覆盖
    # 它们"会让模型给它补一行
    ("设备提示符不当成参数", collect_parameters, (CPU_SCENARIO,),
     ["car-index", "begin-time", "end-time"]),
    # ---- 第三张基准表（列全、但排障目标每行重填，见文件开头） ----
    ("完整列形态的合规样例", check_skill_format,
     (CPU_ALARM_GOOD, CPU_ALARM_SCENARIO), ""),
    # 排障目标每行重填而不是合并：只看"这格非空"会切成 8 个一步的场景。
    # 这张表尾部还拖着 29 列没表头的空列（原表导出来就带着），认列不能被它们带偏
    ("每行重填的排障目标算一个场景", scenario_shape, (CPU_ALARM_SCENARIOS,), [1, 8]),
    ("表尾的空列不影响认列", sorted, (CPU_ALARM_SCENARIO["columns"],),
     ["command", "desc", "detail", "fix", "goal", "impact", "no", "rag",
      "topology", "verify"]),
    # 写作约束里举的例子（如 `<car-index>`）不是每张表都有，模型照搬进正文时，
    # 报错要指出是哪条命令带着它
    ("表里没有的参数要指出是哪条命令", check_skill_format,
     (CPU_ALARM_GOOD.replace("（确认攻击报文类型、接口与 VLAN 信息）",
                             "（可用 `car-index <car-index>` 过滤）", 1),
      CPU_ALARM_SCENARIO),
     "命令 `car-index <car-index>` 里用了参数"),
    # ragIndex 那格写了两条带编号的用途，那是给两条命令各写了一句，不是 ragIndex
    ("多行带编号的用途不当成ragIndex", parse_rag_index,
     ("1. 查看NETCONF查询操作详细统计信息\n2. 查看NETCONF全量同步操作详细统计信息",),
     (None, "1. 查看NETCONF查询操作详细统计信息\n2. 查看NETCONF全量同步操作详细统计信息")),
    # 修复用的 CLI 填在了「修复建议影响性」那格里，照着它写的命令不算编造
    ("影响性列里的修复CLI要放行", check_commands_from_source,
     ("修复：`system-view` → `slot <slot-id>` → `cpu-defend-policy 8`",
      CPU_ALARM_SCENARIO), ""),
    ("哪一列都没有的CLI仍要拦", check_commands_from_source,
     ("修复：`cpu-defend-policy 8 acl 3000`", CPU_ALARM_SCENARIO),
     "在步骤表里没有出现过"),
    # 表里写的是具体值（`cpu-defend-policy 8`），模型自作主张换成了参数：该说的是
    # "照表里的写法写"，说"这条CLI是自己编的、删掉它"会把表里给的修复命令删掉
    ("把表里的具体值换成参数要指出表里的写法", check_skill_format,
     (CPU_ALARM_GOOD.replace("`cpu-defend-policy 8`", "`cpu-defend-policy <car-id>`", 1),
      CPU_ALARM_SCENARIO), "表里这条命令写的是 `cpu-defend-policy 8`"),
    # 给表里的命令补了个过滤条件：去掉多的那段就行，别把命令删了
    ("给表里的命令加了一段要说去掉那段", check_skill_format,
     (CPU_ALARM_GOOD.replace(
         "`display attack-source-trace slot <slot-id> verbose`",
         "`display attack-source-trace slot <slot-id> verbose "
         "time-range from <begin-time> to <end-time>`", 1),
      CPU_ALARM_SCENARIO), "把多出来的那段去掉"),
    ("另一张表照抄方括号要拦", check_skill_format,
     (CPU_GOOD.replace("`display cpu-usage service slot <slot-id>`",
                       "`display cpu-usage service [ slot slot-id ]`", 99),
      CPU_SCENARIO), "语法记号"),
    # 多条命令的格子常带列表编号，编号是填表人排版用的，不是命令的一部分——
    # 不剥掉就会要求正文里出现一条 `1.` 开头的命令，模型怎么写都过不了
    ("命令行格里的列表编号不算命令的一部分", step_commands,
     ({"command": "1. display users\n2、display cpu-usage process\n- display this"},),
     ["display users", "display cpu-usage process", "display this"]),

    # 模型给的根因清单要先规整：一整句话不是根因名，重复的只留一条，
    # 步骤号要能换算回步骤表的行号（报错时要指出处）
    ("根因清单：整句话丢掉、重复只留一条、补上行号", normalize_root_causes,
     ([{"step": 8, "cause": "srlist超限", "normal": False},
       {"step": 8, "cause": " srlist超限 ", "normal": False},
       {"step": 3, "cause": "如果ipv6-family sr-policy地址族未配置则返回该结论并继续执行第4步",
        "normal": False},
       {"step": 1, "cause": "", "normal": False},
       "不是字典",
       {"step": 1, "cause": "SRv6 TE Policy状态正常", "normal": True}], scenario),
     [{"step": "8", "row": 9, "text": "srlist超限", "normal": False},
      {"step": "1", "row": 2, "text": "SRv6 TE Policy状态正常", "normal": True}]),

    # `bgp route-learning` 跟的是子关键字而不是实例名，不该被当成"写死了具体值"
    ("子关键字不当成写死的值", check_hardcoded_operands,
     ("```\nbgp route-learning acceleration enable\n```",), ""),
    ("实例名当成写死的值", check_hardcoded_operands,
     ("```\nbgp 100\n```",), "写成了具体值"),
]


def run() -> int:
    failures = 0
    for name, func, args, expect in DIRECT_CASES:
        got = func(*args)
        if not isinstance(expect, str):     # 返回值不是错误说明而是数据
            ok = got == expect
        else:
            ok = (got == "") if expect == "" else (expect in got)
        if not ok:
            failures += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if got and isinstance(got, str):
            print(f"        → {got}")
        if not ok:
            print(f"        实际: {got!r}\n        期望: {expect!r}")
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
    total = len(CASES) + len(DIRECT_CASES)
    print(f"\n{total - failures}/{total} 通过")
    return failures


# GOOD 同时被 mock 跑用作假的模型回复，所以这个模块要能被 import 而不自己跑起来
if __name__ == "__main__":
    sys.exit(1 if run() else 0)
