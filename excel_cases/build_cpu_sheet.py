#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 CPU 利用率超限的排障步骤表样例 xlsx。

和 `build_sample_sheet.py` 那张表**列不一样**，留着就是为了守住这一点：

  - 没有「排障目标」列（告警名不在表里，只能退回用 sheet 名当场景名）；
  - 没有「回显」「配置修复建议」「修复建议影响性」「修复验证」这四列；
  - 「组网场景」列是合并单元格，但里面是空的（原表放的是截图）——所以场景分块
    只能靠步骤编号回到 1；
  - ragIndex 列里只写用途、不写编号（`查看单板上进程CPU占用率`）；
  - 一格里两条命令，还带着 `1.` `2.` 的列表编号；
  - 命令里的参数**不带尖括号**（`display cpu-usage process process-id`），
    可选参数用 `[ ]` 圈着（`display cpu-usage process [ slot slot-id ]`）。

以上每一条都曾让整张表跑不出来，逐条见 excel_skill_distill_pipeline 里的注释。

xlsx 是二进制，进了 git 就看不出 diff，所以表的内容以这个脚本为准：改内容改这里
再重跑，评审时看这个文件而不是去开 Excel。

用法：
  python3 excel_cases/build_cpu_sheet.py [输出路径]
"""
import os
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill
from openpyxl.utils import get_column_letter

DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "CPU利用率超限步骤表.xlsx")

# 表里没有告警名，场景名只能从 sheet 名来
SHEET_NAME = "CPU利用率超限"

HEADERS = [
    "组网场景\n按需，协议类可能涉及对端，截图放进来即可",
    "排障步骤编号",
    "排障步骤描述",
    "步骤详细描述",
    "本步骤需要使用的命令行的编号及使用目的（ragIndex）",
    "命令行",
]

COLUMN_WIDTHS = [18, 10, 24, 72, 28, 44]
MONOSPACE_COLUMNS = {5}   # 0基：命令行

TEXT_FONT = "Arial"
MONO_FONT = "Consolas"

# 每行：步骤编号、排障步骤描述、步骤详细描述、ragIndex、命令行
STEPS = [
    (1, "查看单板上进程CPU占用率",
     "诊断视图下，执行display cpu-usage process [ slot slot-id ]，查看进程的CPU占用率。"
     "找到前3高的进程。如果其中有PROTO2进程跳到第3步继续执行。如果其中有CFG进程跳到第6步继续执行。"
     "如果是其他进程则继续执行第2步。",
     "查看单板上进程CPU占用率",
     "display cpu-usage process [ slot slot-id ]"),
    (2, "查看组件的CPU占用率",
     "诊断视图下，执行display cpu-usage process process-id命令，查看组件的CPU占用率。"
     "找到前3高的组件后需要联系对应组件维护人员继续确认原因；初步定位原因是路由协议震荡。"
     "并继续执行第12步。",
     "查看组件的CPU占用率",
     "display cpu-usage process process-id"),
    (3, "查看组件的CPU占用率",
     "诊断视图下，执行display cpu-usage process [ slot slot-id ]和"
     "display cpu-usage process process-id命令，分别查看CPU占用率高的进程和组件，"
     "发现PROTO2进程、CACHE组件CPU占用率高，一般是单板报文上送过多导致。",
     "查看组件的CPU占用率",
     "display cpu-usage process [ slot slot-id ]\ndisplay cpu-usage process process-id"),
    (4, "查看CP-CAR上送计数",
     "诊断视图下，执行display cpu-defend statistics-all slot 1命令查看单板的CP-CAR上送统计，"
     "连续查看CP-CAR上送计数，确认是哪类报文上送多导致CPU高。",
     "查看CP-CAR上送计数",
     "display cpu-defend statistics-all slot slot-id"),
    (5, "查看单板攻击溯源的详细信息",
     "执行display attack-source-trace slot slot-id verbose命令查看单板攻击溯源的详细信息，"
     "确认攻击报文类型和接口、VLAN信息等。排查报文来源，确认报文上送过多原因，降低报文上送数量。"
     "设备可以临时通过部署白名单或调整CAR动作降低CPU占用率。"
     "该步骤执行完后已经完成定位，认为是报文攻击导致CPU冲高。\n\n"
     "通过CP-CAR上送计数的查询，查看Dropped Packets列，针对丢包通道，查看Index列获取car-index，"
     "此时，如希望查看特定car index或特定时间内的攻击溯源详细信息，可以通过"
     "display attack-source-trace slot slot-id verbose "
     "[ { car-index <car-index> } | { time-range from <begin-time> [ to <end-time> ] } "
     "完成对攻击溯源详细信息的过滤",
     "查看单板攻击溯源的详细信息",
     "display attack-source-trace slot slot-id verbose"),
    (6, "查看网管通过SNMP/NETCONF查询业务",
     "查看网管通过SNMP/NETCONF查询了哪些业务。在CPU占用率高的时候，尽可能多次执行查询命令，"
     "回显次数多的就是网管采集的内容。\n"
     "诊断视图下，执行display cmf-info luascript history process process-id命令，采集执行的业务脚本。",
     "查看网管通过SNMP/NETCONF查询业务",
     "display cmf-info luascript history process process-id"),
    (7, "采集耗时长的业务脚本",
     "诊断视图下，执行display cmf-info luascript longtime process process-id命令，采集耗时长的业务脚本。",
     "采集耗时长的业务脚本",
     "display cmf-info luascript longtime process process-id"),
    (8, "查看网管采集行为统计信息",
     "根据SNMP的报文统计信息，查看哪些SNMP网管执行了采集行为，哪些业务脚本有耗时。\n"
     "诊断视图下，执行display snmp-agent statistics nms命令，查看网管采集行为统计信息。",
     "查看网管采集行为统计信息",
     "display snmp-agent statistics nms"),
    (9, "查看设备MIB节点的统计信息",
     "诊断视图下，执行display snmp-agent statistics mib命令，"
     "查看昨天与今天网管访问设备MIB节点的统计信息。",
     "查看设备MIB节点的统计信息",
     "display snmp-agent statistics mib"),
    (10, "查看MIB节点请求超时的统计信息",
     "诊断视图下，执行display snmp-agent statistics mib timeout命令，查看MIB节点请求超时的统计信息，"
     "根据超时的节点OID，找对应业务模块分析耗时原因。",
     "查看MIB节点请求超时的统计信息",
     "display snmp-agent statistics mib timeout"),
    (11, "根据记录确认冲高源",
     "根据用户的登录记录，查看哪些NETCONF网管在执行全量或增量同步。\n"
     "方式一：NETCONF是基于SSH的TCP链接，可以通过用户登录日志查看哪些用户执行了登录操作，"
     "即可找到对应用户的IP地址。\n\n"
     "SSH/5/SSH_USER_LOGIN: The SSH user succeeded in logging in. (ServiceType=[ServiceType], "
     "UserName=[UserName], UserAddress=[UserAddress], LocalAddress=[LocalAddress], "
     "VPNInstanceName=[VPNInstanceName])\n"
     "SSH/5/SSH_USER_LOGOUT: The SSH user logged out. (ServiceType=[ServiceType], "
     "LogoutReason=[LogoutReason], UserName=[UserName], UserAddress=[UserAddress], "
     "LocalAddress=[LocalAddress], VPNInstanceName=[VPNInstanceName])\n"
     "方式二：如果网管用户同步行为正在进行中，可以执行display users命令，查看登录用户的IP地址。"
     "NETCONF占用的是NCA通道，与普通的VTY通道有区别。\n\n"
     "<HUAWEI>  display users\n"
     "User-Intf    Delay    Type   Network Address     AuthenStatus    AuthorcmdFlag\n"
     "+ 34  VTY 0   00:00:00  TEL    10.134.146.150            pass           yes       "
     "Username : YsH_2022\n\n"
     "  35  VTY 1   00:12:17  TEL    10.179.179.127            pass           yes       "
     "Username : YsH_2022\n\n"
     "  36  VTY 2   00:00:00  TEL    10.136.138.221            not pass       no        "
     "Username : Unspecified\n"
     "该步骤执行完后已经定位完成，认为是管理面同步数据导致CPU冲高。",
     "根据记录确认冲高源",
     "NA"),
    (12, "查看CPU占用率高的服务类型",
     "执行display cpu-usage service [ slot slot-id ]命令，查看CPU占用率高的服务类型。"
     "主控板BRAS服务类型、BR_UCM、BR_AAA、BR_RADIUS、BR_DACC等组件CPU占用率高，"
     "接口板BRAS服务类型、PPPOE、PPP等组件CPU占用率高，都是由于用户大量上线导致。"
     "如果以上服务类型不符合，停止继续执行后续步骤。认为冲高原因为路由协议震荡。",
     "查看CPU占用率高的服务类型",
     "display cpu-usage service [ slot slot-id ]"),
    (13, "查看用户上线和下线失败原因",
     "查看用户上线和下线失败TOP5原因：\n"
     "1. 诊断视图下，执行display aaa offline-record statistics命令查看2天内，"
     "每10分钟数量最多的5条下线原因。\n"
     "2. 诊断视图下，执行display aaa online-fail-record statistics命令查看2天内，"
     "每10分钟数量最多的5条上线失败原因。",
     "查看用户上线和下线失败原因",
     # 多条命令的格子常常带编号，编号是排版用的、不是命令的一部分
     "1. display aaa offline-record statistics\n2. display aaa online-fail-record statistics"),
    (14, "查看UCM模块下线原因统计信息",
     "如果步骤2无法查看用户下线原因，请在诊断视图下执行display ucm statistics offline-reason命令，"
     "查看UCM模块下线原因统计信息。可以先执行reset ucm statistics offline-reason命令清除UCM的统计信息，"
     "再多次执行display ucm statistics offline-reason命令查看，根据原因数量统计信息确认TOP原因，"
     "建议每间隔10分钟执行一次。\n"
     "该步骤执行完成后认为是业务负载高导致CPU冲高。",
     "查看UCM模块下线原因统计信息",
     "display ucm statistics offline-reason"),
]

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
THIN = Border()


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
        for i, value in enumerate(step, start=2):
            cell = ws.cell(row=row, column=i, value=value)
            font = MONO_FONT if (i - 1) in MONOSPACE_COLUMNS else TEXT_FONT
            cell.font = Font(name=font)
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    # 组网场景整列合并、且**不写内容**：原表那格放的是截图。表里既没有排障目标列、
    # 场景列又是空的，分块只能靠步骤编号——这正是要守住的那条路径。
    ws.merge_cells(start_row=2, start_column=1, end_row=1 + len(STEPS), end_column=1)
    ws.cell(row=2, column=1).alignment = Alignment(wrap_text=True, vertical="top")

    ws.freeze_panes = "A2"
    wb.save(output_path)
    return output_path


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT
    print(f"已生成 {build(path)}（{len(STEPS)} 步）")
