#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成"CPU利用率超限定位"的排障步骤表样例 xlsx（11+1 列的完整形态）。

仓库里三张样例表各守一种表的长相，这张守的是：

  - 「排障目标」列**每行都重填了一遍**，没有用合并单元格——只看"这一格非空"来分块
    的话，一个八步的排障流程会被切成八个一步的场景；
  - 表尾多一列「R23.0是否支持」，是这张表自己加的，认列时要能忽略掉；
  - 「组网场景」填的是 `不涉及`，按占位符读成空；
  - ragIndex 那格写的是**带编号的两条用途**（`1. 查看…` 换行 `2. 查看…`），那是给
    该步的两条命令各写了一句，不是这一步的 ragIndex 编号；
  - 修复用的 CLI 落在「**修复建议影响性**」那格里（填表人填串列了），照着它写出来
    的命令不能被判成模型自己编的；
  - 「命令行」一格里三条命令，前两条带编号、第三条不带，其中一条带 `<mib-id>`。

以上每一条都曾让整张表跑不出来，逐条见 excel_skill_distill_pipeline 里的注释。

xlsx 是二进制，进了 git 就看不出 diff，所以表的内容以这个脚本为准：改内容改这里
再重跑，评审时看这个文件而不是去开 Excel。

用法：
  python3 excel_cases/build_cpu_alarm_sheet.py [输出路径]
"""
import os
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "CPU利用率超限定位步骤表.xlsx")

SHEET_NAME = "CPU利用率超限定位"

HEADERS = [
    "排障目标\n1、有告警或者事件写进来\n2、提供一种故障构造方法",
    "组网场景\n按需，协议类可能涉及对端，截图放进来即可",
    "排障步骤编号",
    "排障步骤描述\n",
    "步骤详细描述",
    "本步骤需要使用的命令行的编号及使用目的（ragIndex）",
    "命令行",
    "配置修复建议，定位到根因的步骤里给出，如果需要人工修复就给出建议； "
    "如果可以自修复（配置错误）给出CLI，包括CLI生成规则和参数分配规则，细化到具体参数。",
    "修复建议影响性\n修改配置的，如果影响性大，则需要可靠性保障<比如仿真>",
    "修复验证，怎么验证",
    "R23.0是否支持\n依据：R23.0未EOS",
]

COLUMN_WIDTHS = [30, 14, 10, 24, 76, 30, 44, 40, 34, 16, 14]
MONOSPACE_COLUMNS = {6}   # 0基：命令行

TEXT_FONT = "Arial"
MONO_FONT = "Consolas"

# 每行都重填一遍的排障目标（原表就是这么填的，没有合并）
GOAL = "CPU利用率超限定位\nhwCPUUtilizationRisingAlarm告警\n构造方法"
TOPOLOGY = "不涉及"

# 每行：编号、步骤描述、步骤详细描述、ragIndex、命令行、配置修复建议、影响性、修复验证
STEPS = [
    (1, "查看单板上进程CPU占用率",
     "诊断视图下，执行display cpu-usage process [ slot slot-id ]，查看进程的CPU占用率。"
     "找到前3高的进程。如果其中有PROTO2进程跳到第3步继续执行。如果其中有CFG进程跳到第6步继续执行。"
     "如果是其他进程则继续执行第2步。",
     "查看单板上进程CPU占用率",
     "display cpu-usage process [ slot slot-id ]", "", "", ""),
    (2, "查看组件的CPU占用率",
     "诊断视图下，执行display cpu-usage process process-id命令，查看组件的CPU占用率。"
     "找到前3高的组件后需要联系对应组件维护人员继续确认原因；初步定位原因是路由协议震荡。"
     "并继续执行第12步。",
     "查看组件的CPU占用率",
     "display cpu-usage process process-id", "", "", ""),
    (3, "查看组件的CPU占用率",
     "诊断视图下，执行display cpu-usage process [ slot slot-id ]和"
     "display cpu-usage process process-id命令，分别查看CPU占用率高的进程和组件，"
     "发现PROTO2进程、CACHE组件CPU占用率高，一般是单板报文上送过多导致。",
     "查看组件的CPU占用率",
     "display cpu-usage process [ slot slot-id ]\ndisplay cpu-usage process process-id",
     "", "", ""),
    (4, "查看CP-CAR上送计数",
     "诊断视图下，执行display cpu-defend statistics-all slot 1命令查看单板的CP-CAR上送统计，"
     "连续查看CP-CAR上送计数，确认是哪类报文上送多导致CPU高。",
     "查看CP-CAR上送计数",
     "display cpu-defend statistics-all slot slot-id", "", "", ""),
    (5, "查看单板攻击溯源的详细信息",
     "执行display attack-source-trace slot slot-id verbose命令查看单板攻击溯源的详细信息，"
     "确认攻击报文类型和接口、VLAN信息等。排查报文来源，确认报文上送过多原因，降低报文上送数量。"
     "设备可以临时通过部署白名单或调整CAR动作降低CPU占用率。"
     "该步骤执行完后已经完成定位，认为是报文攻击导致CPU冲高。",
     "查看单板攻击溯源的详细信息",
     "display attack-source-trace slot slot-id verbose",
     "",
     # 修复用的 CLI 填在了「影响性」这一格里
     "应用防攻击策略：\n\n执行命令system-view，进入系统视图。\n\n"
     "执行命令slot 1，进入slot视图。\n"
     "执行命令cpu-defend-policy 8，在接口板上应用防攻击策略。",
     "告警消除"),
    (6, "确认NETCONF查询/同步操作导致CPU高的业务模块",
     "步骤一：查看NETCONF查询操作耗时模块\n"
     "诊断视图下，执行display netconf data-flow statistics verbose命令查看最近执行的"
     "NETCONF查询操作各业务查询次数及耗时情况，查询结果可能会包含多次查询操作的记录，"
     "最近的记录在最上方，每条记录中都有Operation Received Timestamp字段（表示设备收到查询操作的时间）"
     "和Operation completed Timestamp（本次查询操作结束时间），根据该时间范围判断CPU冲高时间点"
     "是否在该范围内。确认记录后基于YANG模块粒度进行比较各业务模块，通过比较Processing Time字段"
     "和Record Number字段，找到耗时长度前三和记录个数前三的YANG模块确定CPU占用较多模块。\n\n"
     "步骤二：查看NETCONF全量同步操作耗时模块\n"
     "诊断视图下，执行display netconf sync-full statistics verbose命令查看NETCONF全量同步操作"
     "业务查询次数及耗时情况，查询结果可能包含多次全量同步操作的记录，最近的记录在最上方，"
     "每条记录中都有Operation Received Timestamp字段（表示设备收到查询操作的时间）"
     "和Operation completed Timestamp（本次查询操作结束时间），根据该时间范围判断CPU冲高时间点"
     "是否在该范围内。确认记录后基于YANG模块粒度比较各业务模块，通过比较CmfTime和RecordNumber，"
     "找到耗时长度前三和记录个数前三的YANG模块确定CPU占用较多模块。\n\n"
     "步骤三：若上述步骤均无法获取有效结论，则跳转到排障步骤编号继续分析",
     "1. 查看NETCONF查询操作详细统计信息，获取查询耗时和查询记录个数TOP3的YANG模块\n"
     "2. 查看NETCONF全量同步操作详细统计信息，获取耗时和查询记录个数TOP3的YANG模块",
     "1. display netconf data-flow statistics verbose\n"
     "2. display netconf sync-full statistics verbose",
     "修复建议：\n"
     "1. 如果CPU冲高时间点在查询操作范围内，根据会话信息获取控制器IP地址（Host Identifier字段）"
     "从而找到对应控制器，并由对应控制器维护人员在控制器进行NETCONF查询操作时，"
     "可以去勾选处理时间和记录TOP3的YANG模块进行查询操作。\n"
     "2. 如果CPU冲高时间点在全量同步操作范围内，根据会话信息获取控制器IP地址（Host Identifier字段）"
     "从而找到对应控制器，并由对应控制器维护人员在控制器进行NETCONF全量同步操作时，"
     "去勾选处理时间和记录TOP3的YANG模块后再次进行全量同步操作。",
     "", ""),
    (7, "确认SNMP MIB操作导致CPU高的MIB节点",
     "步骤一：查看设备MIB节点的统计信息\n"
     "诊断视图下，执行display snmp-agent statistics mib命令，记录MIB节点总数（Total MIB number）"
     "和各mib节点统计信息（包括MIB Node名称与每个MIB节点执行SET/GET/GETNEXT/GETBULK操作的总计数），"
     "间隔30s后，再次查询display snmp-agent statistics mib命令，查看mib节点总数是否发生变更。"
     "若未发生变更，则并非SNMP操作导致的CPU高；否则将最新的各mib节点统计计数信息与前一次信息进行比较，"
     "获取执行操作计数差异TOP3的MIB节点。\n\n"
     "步骤二：查看MIB节点请求超时的统计信息\n"
     "诊断视图下，执行display snmp-agent statistics mib timeout命令，查看MIB节点请求超时的统计信息，"
     "默认MIB节点处理超过5s会记录该信息。根据超时的节点VB[0]并利用"
     "display snmp-agent mib node <mib-id>获取该MIB节点的name.\n\n"
     "步骤三：若上述步骤均无法获取有效结论，则跳转到排障步骤编号继续分析",
     "1. 先后2次查看SNMP MIB统计信息，获取2次查询间隔中操作次数TOP3的MIB节点\n"
     "2. 查看单个MIB节点请求超时的统计信息，获取出现处理时间超长的MIB节点",
     "1. display snmp-agent statistics mib\n"
     "2. display snmp-agent statistics mib timeout\n"
     "display snmp-agent mib node <mib-id>",
     "修复建议：\n"
     "根据获取到操作次数TOP3的MIB节点，根据节点中SourceIP/Port/VPNId字段信息确认控制器IP，"
     "找到对应控制器由对应的维护工程师确认SNMP操作时是否可以去勾选对应的MIB节点进行操作。",
     "", ""),
    (8, "采集耗时长的业务脚本",
     "若以上操作均未找到导致CPU冲高的业务模块则进行如下操作：\n"
     "诊断视图下，执行display cmf-info luascript longtime process process-id命令，"
     "根据CPU冲高时间点，筛选查询结果中的StartRunTime和FinishRunTime时间包含该范围的记录，"
     "根据SptFileName记录的脚本名称找到对应业务模块确认",
     "采集CPU冲高期间耗时长的业务脚本确定业务模块",
     "display cmf-info luascript longtime process process-id",
     "修复建议：找到CPU冲高期间业务脚本执行时间超长的脚本，根据脚本明确找对应业务模块确认。",
     "", ""),
]

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")


def build(output_path: str) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME

    for i, header in enumerate(HEADERS, start=1):
        cell = ws.cell(row=1, column=i, value=header)
        cell.font = Font(name=TEXT_FONT, bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.column_dimensions[get_column_letter(i)].width = COLUMN_WIDTHS[i - 1]

    for offset, step in enumerate(STEPS):
        row = 2 + offset
        # 排障目标与组网场景每行重填，不合并
        for column, value in ((1, GOAL), (2, TOPOLOGY)):
            cell = ws.cell(row=row, column=column, value=value)
            cell.font = Font(name=TEXT_FONT)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        for i, value in enumerate(step, start=3):
            cell = ws.cell(row=row, column=i, value=value)
            font = MONO_FONT if (i - 1) in MONOSPACE_COLUMNS else TEXT_FONT
            cell.font = Font(name=font)
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    ws.freeze_panes = "A2"
    wb.save(output_path)
    return output_path


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT
    print(f"已生成 {build(path)}（{len(STEPS)} 步）")
