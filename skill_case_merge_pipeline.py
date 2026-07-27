#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把新增故障案例增量并入既有skill库。

流程：
  1. 读取案例JSON（cases/*.json），跳过无正文条目与标注"已废弃/已从场景基线中去除"的条目，
     按"故障类型"归成案例组；
  2. 定位阶段：把既有skill清单（路径+description+小节标题）和案例摘要交给模型，
     判断每个案例组应并入哪个既有skill，或需要新建skill；
  3. 合并阶段：按目标skill把案例组归拢（同一目标只调一次模型），
     把目标skill全文与案例详情交给模型，产出"已覆盖 / 追加小节 / 新建skill"的判定与内容；
  4. 应用阶段：在主进程串行写文件（追加或新建），并输出 skill_change_report.md 变更说明。

用法：
  python3 skill_case_merge_pipeline.py            # 按文件末尾main()的默认参数运行
"""
import json
import os
import re
import sys
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path

from skill_self_distill_pipeline import call_model_with_retry, extract_markdown_content

# 案例条目标注了这些字样时不再并入skill（场景已下线/已确认不成立）
RETIRED_MARKERS = ("已废弃", "已从场景基线中去除")

# 人工指定某个案例组并入哪个skill，跳过定位阶段的模型调用。
# 定位阶段判"新建"时是让模型自己编路径，同一批案例重跑可能编出不同的目录和文件名；
# 路径一旦定下来就写到这里，避免重跑在两个位置各建一篇内容重复的skill。
# 键为案例组的"故障类型"，值为"一级目录/二级名称.md"。
TARGET_OVERRIDES = {
    "链路性能越限": "故障处理：QoS/切片链路带宽越限故障案例.md",
}

# 与主蒸馏流水线保持一致的写作约束，追加/新建的内容必须同样遵守
WRITING_RULES = """- 输出面向网管agent执行，凡是收集信息、联系技术支持、提交给工程师这类动作，整个步骤删除，并删除其它步骤对它的引用。
- 输出全文禁止出现"联系技术支持"、"寻求技术支持"、"提交给工程师"、"收集信息并联系"、"联系业务侧/安全侧/运维人员处理"等表述，排障步骤穷尽后直接结束。
- 案例明确写了"NA，人工远程修复"或"可以诊断，无法修复/无法定位"的根因，skill里只写到根因判定为止，不要编出修复步骤，也不要写"需在对端设备上添加正确配置"这类没有具体执行手段的说法。
- 抓包分析（wireshark等）、仿真验证、重启协议进程这类agent执行不了或有风险的动作，不要写成步骤。
- 管控接口只写案例里给出的信息（路径、案例中提到的入参）。案例没给HTTP方法就不要猜，更不要写"根据具体网管API定义"这类模糊说法；入参字段名案例只给了中文描述时，就用中文描述，不要自己编英文字段名。
- 案例中形如"—— 待确认xxx"、"请刘瑞xxx"、"求助设备专家"、人名工号等内部讨论备注，一律不要写进skill。
- skill的读者是执行排障的agent，它看不到本次的输入案例，也不知道什么是"目标skill"。正文里禁止出现"按案例描述"、"根据案例描述"、"案例中未给出"、"新案例"、"原skill"、"本skill主要针对xxx"这类交代来源或自我说明的话；拿不准某个动作能不能做时，直接写你确定的部分，不要把犹豫写进正文。
- 案例给出了某个分支的检查步骤时（如"若不是切片接口则检查xxx"），该分支要照写，不要用"参考常规xxx排障"一句话带过。
- 追加或新建的内容属于目标skill自身，引用同一篇skill里的其它小节时直接写"参考本文场景X"，不要用[路径.md]形式引用目标skill自己。
- 引用其它分类的skill时，用方括号包裹skill目录清单中的相对路径，如：参考[故障处理：IP路由/BGP故障案例.md]继续排查；清单中没有的文档只保留纯文字说明，不要输出链接。
- 命令回显不要大段照搬，讲清要关注回显里的哪些字段即可；设备配置样例保留必要的几行即可。
- 案例中的管控接口（如 POST /rest/xxx）属于修复手段，可以保留，写清接口用途和关键入参。"""

LOCATE_PROMPT_TEMPLATE = """你是IPRAN网络运维专家，正在维护一套供网管agent使用的排障skill库。现在有一批新的故障案例，需要判断它应该并入哪个既有skill，还是需要新建skill。

# 既有skill清单
<skill_index>

# 待归类的新案例
<case_brief>

# 判断要求
- 优先并入既有skill：只要案例的故障对象/协议与某个skill属于同一排障主题（例如同为BGP邻居类、同为BFD类、同为L2VPN业务类、同为MPLS隧道类），就归入该skill。
- 只有当清单里没有任何skill覆盖该案例所属的排障主题时，才新建skill。
- 新建时一级目录必须优先复用清单中已有的一级目录名，确实不属于任何既有一级分类时才新增；二级名称用中文，命名风格与清单保持一致。
- 目标路径格式固定为"一级目录/二级名称.md"。

# 输出格式
只输出一个json代码块，不要输出任何其它内容：
```json
{"action": "existing", "target": "一级目录/二级名称.md", "reason": "一句话理由"}
```
action取值：existing表示并入清单中已存在的skill，new表示新建skill。
"""

MERGE_PROMPT_TEMPLATE = """你是IPRAN网络运维专家，正在把新增的故障案例并入一套供网管agent使用的排障skill库。请判断这些新案例是否已被目标skill覆盖，并给出需要的改动。

# 目标skill：<target_path>
<target_skill>

# 新增故障案例
<case_detail>

# skill目录清单
跨分类引用时必须使用清单中的路径，格式为[路径]：
<skill_catalog>

# 判断规则
- covered：新案例的故障现象、根因判据、修复动作，目标skill正文已经全部讲到（用词不同但语义等价也算已覆盖）。此时不要输出任何markdown内容。
  判covered前必须逐项确认下面几点在skill里都有，只要有一项没有就不能判covered，应判append：
  1. 新案例的**触发告警**（如hwSrPolicyDown、PW VC Down）在skill里出现过，agent能从告警找到对应排查入口；
  2. 新案例给出的**管控接口/API**（如 PATCH /rest/xxx）在skill里出现过——skill只写了等价的设备CLI不算覆盖，网管agent走的是接口；
  3. 新案例的**检查范围**skill里覆盖到了，例如案例要求查源宿两端网元、而skill只查本端，就不算覆盖。
- append：目标skill已存在，但新案例带来了它没有的根因、判据、命令、修复动作或管控接口。此时只输出需要追加的新小节，不要重复skill里已有的内容，也不要重写整篇skill。
- create：目标skill不存在（下方"目标skill"为"（不存在，需要新建）"），需要按新案例写一篇完整的skill。

# 内容要求
<writing_rules>
- append时：输出一个或多个以"## "开头的小节，小节标题包含根因名称；追加内容会被拼到目标skill文件的**末尾**，所以小节标题不能和目标skill里已有的"## "小节同名（同名会让文件里出现两个同名小节），也不要接着已有小节的编号往下写（如已有小节写到步骤4就从步骤5开始）——每个新小节都是独立的一节，步骤从1开始；不要输出frontmatter（--- name/description ---）；不要输出"# "一级标题；即使目标skill里已有"## "小节，新小节也必须用"## "而不是"### "；步骤编号在小节内部从1开始。追加内容的格式必须形如：

  ## 场景：ISIS System ID冲突
  （一句话说明该场景的现象与触发告警）
  1. 第一步……
  2. 第二步……

  ## 场景：另一个根因
  ……
- create时：输出完整skill，以frontmatter开头（name为英文小写+连字符，description为一句话简介），正文以一级标题"# "开始。一级标题写排障主题名（如"# PWE3故障排障指南"），不要把目标skill的文件路径当标题（不要写成"# 故障处理：VPN/PWE3故障案例"）。

# 输出格式
先输出一个json代码块给出判定，再按需输出一个markdown代码块给出内容（action为covered时不输出markdown代码块）：
```json
{"action": "covered|append|create", "reason": "一句话说明判定理由", "change_summary": "一句话概括本次改动", "sections": ["新增的小节标题"]}
```
```markdown
（append时为追加的小节，create时为完整skill全文）
```
"""


def extract_json_block(text: str) -> dict:
    """从模型回复中取出判定用的json对象，解析失败时抛异常触发重试。"""
    match = re.search(r"```json\s*(.+?)\s*```", text, re.DOTALL)
    raw = match.group(1) if match else text[text.find("{"):text.rfind("}") + 1]
    if not raw.strip():
        raise ValueError("回复中没有找到json内容")
    return json.loads(raw)


def _locate_extractor(text: str) -> str:
    try:
        decision = extract_json_block(text)
    except (ValueError, json.JSONDecodeError):
        return ""
    return text if decision.get("target") else ""


def normalize_append_content(content: str) -> str:
    """把追加内容规整成"若干个 ## 小节"。

    模型常见的两种偏差：给整段追加内容套一个 `# 一级标题`，或者因为目标skill
    里已有 `## ` 小节而把新场景写成 `### `。这两种都只是层级问题，内容本身可用，
    直接抬到 `## ` 即可，不必判失败重跑。
    """
    content = re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, count=1, flags=re.DOTALL)
    lines = content.strip().split("\n")
    while lines and (not lines[0].strip() or re.match(r"^# +\S", lines[0])):
        lines.pop(0)
    content = "\n".join(lines).strip()
    if not re.search(r"^## +\S", content, re.MULTILINE):
        # 没有##但有更深层级时整体上提一级，直到出现##小节
        while re.search(r"^### +\S", content, re.MULTILINE):
            content = re.sub(r"^#(#+ +\S)", r"\1", content, flags=re.MULTILINE)
            if re.search(r"^## +\S", content, re.MULTILINE):
                break
    return content.strip()


def section_headings(markdown: str) -> list:
    """取出markdown里所有二级小节标题。"""
    return [h.strip() for h in re.findall(r"^## +(.+)$", markdown or "", re.MULTILINE)]


def prepare_merge_content(reply: str, existing_headings: list = None,
                          target_path: str = "") -> tuple:
    """从合并阶段的回复里取出 (判定, 规整后的内容, 错误说明)。

    existing_headings 为目标skill已有的二级小节标题，用于挡住"续写既有小节"。
    """
    decision = extract_json_block(reply)
    action = decision.get("action")
    if action == "covered":
        return decision, "", ""
    content = extract_markdown_content(reply)
    if action == "append":
        content = normalize_append_content(content)
    return decision, content, check_generated_content(action, content, existing_headings,
                                                     target_path)


# 落盘前必须挡住的两类措辞，各自给出对应的改法。
BANNED_CONTENT_PATTERNS = [
    # 只有本次流水线才知道"案例""目标skill"是什么，排障时的agent看不到；
    # 正文里出现这些词说明模型在向流水线交代，而不是在写排障步骤。
    (r"按案例描述|根据案例描述|案例(中|里)(未|没有)|新案例|原skill|目标skill"
     r"|本?\s*[Ss]kill(主要|仅|只)(针对|支持|负责|写到)|此处假设|Agent(主要|仅|只)"
     r"|保留原有|更新步骤|新增步骤\s*\d|（原步骤|保持不变）",
     "skill的读者是排障agent，看不到案例、也不知道什么是目标skill，需改写为直接的排障说明"),
    # agent执行不了的动作，写成步骤等于把排障流程堵死在这一步。
    (r"[Ww]ireshark|抓包",
     "网管agent执行不了抓包，不要写成排障步骤"),
]


def check_generated_content(action: str, content: str, existing_headings: list = None,
                            target_path: str = "") -> str:
    """检查生成内容是否可直接落盘，返回错误说明（空串表示通过）。"""
    content = (content or "").strip()
    if action == "covered":
        return ""
    if not content:
        return f"action={action} 但没有生成内容"
    for pattern, advice in BANNED_CONTENT_PATTERNS:
        hit = re.search(pattern, content)
        if hit:
            return f"正文出现了'{hit.group(0)}'：{advice}"
    if target_path and re.search(r"\[[^\[\]\n]*" + re.escape(target_path.split("/")[-1]) + r"\]", content):
        return f"引用了目标skill自身[{target_path}]，同一篇内应写成'参考本文场景X'"
    if action == "append":
        if content.startswith("---"):
            return "追加内容里带了frontmatter，应只输出小节"
        new_headings = section_headings(content)
        if not new_headings:
            return "追加内容里没有以'## '开头的小节"
        # 追加内容整段拼到文件末尾，因此必须从第一个"## "小节开始：出现在它之前的
        # 任何文字都会变成挂在上一小节下的游离内容（模型常在这里写"（保留原有
        # 步骤1-3，更新步骤7）"这类改哪几步的编辑说明）。
        if not content.startswith("## "):
            stray = content.split("\n## ")[0].strip().replace("\n", " ")
            return f"第一个'## '小节之前还有内容，追加内容必须从小节标题开始；多出的部分: {stray[:100]!r}"
        # 与既有小节同名会让文件里出现两个同名小节；首个步骤编号不是1，
        # 说明模型在续写既有小节而不是新起一节。
        dup = [h for h in new_headings if h in set(existing_headings or ())]
        if dup:
            return f"追加的小节与目标skill已有小节同名: {'、'.join(dup)}"
        for block in re.split(r"^#{2,} +.+$", content, flags=re.MULTILINE)[1:]:
            first = re.search(r"^\s*(\d+)[.、)]", block, re.MULTILINE)
            if first and first.group(1) != "1":
                return f"追加小节的步骤编号从{first.group(1)}开始，应为续写既有小节，须改为独立小节且从1开始"
        return ""
    if action == "create":
        if not re.match(r"^---\s*\n", content):
            return "新建skill缺少frontmatter"
        return ""
    return f"未知的action: {action!r}"


def make_merge_extractor(existing_headings: list, target_path: str = ""):
    """内容不合规时抛异常，让call_model_with_retry重试，并把原因带进最终报错。"""
    def _extractor(text: str) -> str:
        _, content, error = prepare_merge_content(text, existing_headings, target_path)
        if error:
            raise ValueError(f"{error}；提取到的内容开头: {content[:120]!r}")
        return text
    return _extractor


def normalize_case(entry: dict) -> dict:
    """把一条案例条目规整为统一字段；仅有标题的补充页返回None。"""
    if entry.get("type") == "supplementary" or not entry.get("故障类型"):
        return None
    return {
        "slide": entry.get("slide"),
        # 补充信息带来的案例不一定对应PPT页码，用“来源”字段说明出处
        "origin": entry.get("来源", "").strip(),
        "fault_type": entry.get("故障类型", "").strip(),
        "root_cause": entry.get("根因类型", "").strip(),
        "alarm": entry.get("触发告警", "").strip(),
        "diagnosis": entry.get("故障诊断", "").strip(),
        "solution": entry.get("方案生成", "").strip(),
        "dependency": entry.get("对管控依赖", "").strip(),
        "note": entry.get("备注", "").strip(),
    }


def case_origin(case: dict) -> str:
    """案例出处：优先用"来源"字段，其次用PPT页码。"""
    if case.get("origin"):
        return case["origin"]
    return f"第{case['slide']}页"


def is_retired(case: dict) -> str:
    """案例被标注为下线/废弃时返回命中的标记，否则返回空串。"""
    text = f"{case['note']} {case['diagnosis']}"
    for marker in RETIRED_MARKERS:
        if marker in text:
            return marker
    return ""


def load_cases(cases_path: str) -> tuple:
    """返回 (有效案例列表, 跳过条目列表, overview_table)。"""
    with open(cases_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    active, skipped = [], []
    for entry in data.get("fault_scenarios", []):
        case = normalize_case(entry)
        if case is None:
            skipped.append({
                "slide": entry.get("slide"),
                "title": entry.get("raw_title", ""),
                "reason": "仅有标题没有正文（补充页）",
            })
            continue
        marker = is_retired(case)
        if marker:
            skipped.append({
                "slide": case["slide"],
                "title": f"{case['fault_type']}：{case['root_cause']}",
                "reason": f"案例标注为“{marker}”",
            })
            continue
        active.append(case)
    return active, skipped, data.get("overview_table", [])


def match_overview_rows(fault_type: str, overview_table: list) -> list:
    """取出概览表中与该故障类型对应的行（故障定义与故障类型互为子串即认为匹配）。"""
    rows = []
    for row in overview_table:
        definition = row.get("故障定义", "").strip()
        if definition and (definition in fault_type or fault_type in definition):
            rows.append(row)
    return rows


def _normalize_root_cause(text: str) -> str:
    """比对根因用的归一化：去掉空格/连字符并转小写，让"人工关闭SR Policy"与
    "人工关闭SR-Policy"、"BGP Router ID冲突"与"router-id冲突"能对上。"""
    return re.sub(r"[\s\-_]", "", text).lower()


def _root_cause_matches(row_cause: str, case_cause: str) -> bool:
    """概览行的根因与详情页的根因是否指同一件事。

    两边措辞常有出入（概览写"BGP Router ID冲突"、详情页写"...router-id冲突，
    三个根因中的一种"；概览写"ISIS路由环路"、详情页只写"路由环路"），所以在
    归一化之后用最长公共子串占较短串的比例来判定，而不是要求严格包含。
    """
    a, b = _normalize_root_cause(row_cause), _normalize_root_cause(case_cause)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return _longest_common_substring_len(a, b) / min(len(a), len(b)) >= 0.6


def _longest_common_substring_len(a: str, b: str) -> int:
    prev = [0] * (len(b) + 1)
    best = 0
    for ch_a in a:
        cur = [0] * (len(b) + 1)
        for j, ch_b in enumerate(b, 1):
            if ch_a == ch_b:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def audit_overview_coverage(cases: list, skipped: list, overview_table: list) -> tuple:
    """概览表与详情页理论上一一对应，这里核对实际是否对得上。

    返回 (只有概览行没有可用详情页的行, 有详情页但概览表没列的案例)。
    仅有标题的补充页不算"有可用详情页"，因为蒸馏不出步骤；已下线的详情页
    本就不该出现在概览表里，不参与反向核对。
    """
    skipped_titles = {s["slide"]: s["title"] for s in skipped}
    orphan_rows = []
    for row in overview_table:
        definition = row.get("故障定义", "").strip()
        root_cause = row.get("故障根因", "")
        hit = any(
            (definition in case["fault_type"] or case["fault_type"] in definition)
            and _root_cause_matches(root_cause, case["root_cause"])
            for case in cases
        )
        if not hit:
            note = next((t for _, t in sorted(skipped_titles.items())
                         if _root_cause_matches(root_cause, t)), "")
            orphan_rows.append((definition, root_cause, note))

    orphan_cases = []
    for case in cases:
        hit = any(
            (row.get("故障定义", "").strip() in case["fault_type"]
             or case["fault_type"] in row.get("故障定义", "").strip())
            and _root_cause_matches(row.get("故障根因", ""), case["root_cause"])
            for row in overview_table
        )
        if not hit:
            orphan_cases.append(case)
    return orphan_rows, orphan_cases


def group_cases_by_fault_type(cases: list) -> dict:
    groups = {}
    for case in cases:
        groups.setdefault(case["fault_type"], []).append(case)
    return dict(sorted(groups.items()))


def format_case_brief(fault_type: str, cases: list) -> str:
    lines = [f"故障类型：{fault_type}"]
    for case in cases:
        lines.append(f"- 根因：{case['root_cause']}；告警：{case['alarm'][:80]}")
    return "\n".join(lines)


def format_case_detail(fault_type: str, cases: list, overview_table: list) -> str:
    blocks = []
    overview_rows = match_overview_rows(fault_type, overview_table)
    if overview_rows:
        blocks.append(
            "> 下面的“概览”块来自案例来源的总表，只说明该故障类型要达成的诊断/修复目标，"
            "是纲要而非步骤；总表个别行的措辞与详情页不一致，两者冲突时一律以“案例”块为准。")
    for row in overview_rows:
        blocks.append(
            f"## 概览：{row.get('故障定义', '')} / {row.get('故障根因', '')}\n"
            f"- 故障子类：{row.get('故障子类', '')}\n"
            f"- 诊断目标：{row.get('诊断', '')}\n"
            f"- 修复目标：{row.get('修复', '')}"
        )
    for case in cases:
        blocks.append(
            f"## 案例：{case['fault_type']} —— {case['root_cause']}（来源{case_origin(case)}）\n"
            f"- 触发告警：{case['alarm']}\n"
            f"- 故障诊断：{case['diagnosis']}\n"
            f"- 方案生成：{case['solution'] or '（案例未给出修复方案，skill中只写定位到根因为止）'}\n"
            f"- 对管控依赖：{case['dependency'] or '无'}\n"
            f"- 备注：{case['note'] or '无'}"
        )
    return "\n\n".join(blocks)


def build_skill_index(skill_dir: str) -> list:
    """扫描skill目录，抽取每个skill的相对路径、description与小节标题。"""
    index = []
    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for file in sorted(files):
            if not file.endswith('.md'):
                continue
            path = os.path.join(root, file)
            rel = os.path.relpath(path, skill_dir).replace(os.sep, "/")
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            desc_match = re.search(r"^description\s*:\s*(.+)$", content, re.MULTILINE)
            headings = re.findall(r"^#{1,2} +(.+)$", content, re.MULTILINE)
            index.append({
                "rel_path": rel,
                "description": desc_match.group(1).strip() if desc_match else "",
                "headings": [h.strip() for h in headings],
                "content": content,
            })
    return index


def format_skill_index(skill_index: list) -> str:
    lines = []
    for item in skill_index:
        lines.append(f"- [{item['rel_path']}]：{item['description']}")
        if item["headings"]:
            lines.append(f"  小节：{'；'.join(item['headings'][:15])}")
    return "\n".join(lines) or "（skill库为空）"


def sanitize_target(target: str) -> str:
    """约束模型给出的目标路径：相对路径、以.md结尾、最多两级。"""
    rel = (target or "").strip().strip("[]").replace("\\", "/").lstrip("./")
    parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
    if not parts or not parts[-1].endswith(".md") or len(parts) > 2:
        raise ValueError(f"非法的目标skill路径: {target!r}")
    return "/".join(parts)


def locate_group(args: tuple) -> dict:
    fault_type, cases, skill_index_text, api_url, model_name = args
    prompt = (LOCATE_PROMPT_TEMPLATE
              .replace("<skill_index>", skill_index_text)
              .replace("<case_brief>", format_case_brief(fault_type, cases)))
    print(f"[PID {os.getpid()}] 定位案例组: {fault_type}（{len(cases)} 条）")
    # 定位是分类判断，用temperature=0保证同一批案例重跑结果稳定
    reply = call_model_with_retry(api_url, model_name, prompt,
                                  extractor=_locate_extractor, temperature=0.0)
    if reply.startswith("错误："):
        return {"fault_type": fault_type, "error": reply}
    try:
        decision = extract_json_block(reply)
        return {
            "fault_type": fault_type,
            "target": sanitize_target(decision.get("target")),
            "is_new": decision.get("action") == "new",
            "reason": decision.get("reason", ""),
        }
    except (ValueError, json.JSONDecodeError) as e:
        return {"fault_type": fault_type, "error": f"错误：定位结果解析失败: {e}"}


def merge_bucket(args: tuple) -> dict:
    (target, fault_types, case_detail, target_content, skill_catalog,
     api_url, model_name) = args
    prompt = (MERGE_PROMPT_TEMPLATE
              .replace("<target_path>", target)
              .replace("<target_skill>", target_content or "（不存在，需要新建）")
              .replace("<case_detail>", case_detail)
              .replace("<skill_catalog>", skill_catalog)
              .replace("<writing_rules>", WRITING_RULES))
    print(f"[PID {os.getpid()}] 合并到: {target}（案例组: {'、'.join(fault_types)}）")
    existing_headings = section_headings(target_content)
    reply = call_model_with_retry(api_url, model_name, prompt,
                                  extractor=make_merge_extractor(existing_headings, target))
    result = {"target": target, "fault_types": fault_types, "is_new": not target_content}
    if reply.startswith("错误："):
        result["error"] = reply
        return result
    try:
        decision, content, _ = prepare_merge_content(reply, existing_headings, target)
    except (ValueError, json.JSONDecodeError) as e:
        result["error"] = f"错误：合并结果解析失败: {e}"
        return result

    result["action"] = decision.get("action")
    result["reason"] = decision.get("reason", "")
    result["change_summary"] = decision.get("change_summary", "")
    result["sections"] = decision.get("sections", [])
    result["content"] = content
    return result


def validate_change(result: dict) -> str:
    """应用前再检查一次生成内容，返回错误说明（空串表示通过）。"""
    return check_generated_content(result.get("action"), result.get("content"))


def apply_change(result: dict, skill_dir: str, dry_run: bool) -> None:
    action = result["action"]
    path = os.path.join(skill_dir, *result["target"].split("/"))
    if dry_run:
        print(f"[DRY-RUN] {action} → {path}")
        return
    if action == "append":
        with open(path, 'r', encoding='utf-8') as f:
            existing = f.read().rstrip()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f"{existing}\n\n{result['content'].strip()}\n")
        print(f"已追加: {path}")
    elif action == "create":
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(result["content"].strip() + "\n")
        print(f"已新建: {path}")


def build_report(results: list, skipped: list, locate_errors: list,
                 cases_path: str, skill_dir: str, dry_run: bool) -> str:
    lines = [
        "# 新增案例并入skill变更说明",
        "",
        f"- 案例来源：`{cases_path}`",
        f"- skill目录：`{skill_dir}`",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if dry_run:
        lines.append("- 运行模式：DRY-RUN（未写入skill文件）")
    lines += ["", "## 变更总览", "",
              "| 目标skill | 动作 | 涉及案例组 | 说明 |",
              "| --- | --- | --- | --- |"]
    action_label = {"covered": "已覆盖，无需改动", "append": "追加小节",
                    "create": "新建skill", "failed": "处理失败"}
    for r in results:
        action = "failed" if r.get("error") or r.get("invalid") else r.get("action", "failed")
        note = r.get("error") or r.get("invalid") or r.get("change_summary") or r.get("reason", "")
        lines.append(f"| `{r['target']}` | {action_label.get(action, action)} | "
                     f"{'、'.join(r['fault_types'])} | {note} |")

    failed = [r for r in results if r.get("error") or r.get("invalid")]
    if failed:
        lines += ["", "## 处理失败（未落盘，需重跑）", ""]
        for r in failed:
            lines += [f"### `{r['target']}`", "",
                      f"- 涉及案例组：{'、'.join(r['fault_types'])}",
                      f"- 失败原因：{r.get('error') or r.get('invalid')}"]
            if r.get("content"):
                lines += ["", "<details><summary>模型生成的内容（供排查）</summary>", "",
                          "````markdown", r["content"].strip()[:4000], "````", "",
                          "</details>", ""]

    for r in results:
        if r.get("action") not in ("append", "create") or r.get("invalid") or r.get("error"):
            continue
        lines += ["", f"## {action_label[r['action']]}：`{r['target']}`", "",
                  f"- 涉及案例组：{'、'.join(r['fault_types'])}",
                  f"- 判定理由：{r.get('reason', '')}",
                  f"- 改动概要：{r.get('change_summary', '')}"]
        if r.get("sections"):
            lines.append(f"- 新增小节：{'；'.join(r['sections'])}")
        lines += ["", "<details><summary>改动内容</summary>", "", "````markdown",
                  r["content"].strip(), "````", "", "</details>"]

    covered = [r for r in results if r.get("action") == "covered"]
    if covered:
        lines += ["", "## 已被既有skill覆盖（未改动）", ""]
        for r in covered:
            lines.append(f"- `{r['target']}`（{'、'.join(r['fault_types'])}）：{r.get('reason', '')}")

    if locate_errors:
        lines += ["", "## 定位阶段失败的案例组", ""]
        for item in locate_errors:
            lines.append(f"- {item['fault_type']}：{item['error']}")

    if skipped:
        lines += ["", "## 未并入的案例条目", ""]
        for item in skipped:
            lines.append(f"- 第{item['slide']}页 {item['title']}：{item['reason']}")

    return "\n".join(lines) + "\n"


def main(CASES_PATH, SKILL_DIR, API_URL, MODEL_NAME, WORKERS, REPORT_PATH, DRY_RUN=False):
    print("=" * 60)
    print("新增案例并入skill流水线")
    print("=" * 60)
    print(f"\n案例文件: {CASES_PATH}")
    print(f"skill目录: {SKILL_DIR}")
    print(f"模型: {MODEL_NAME} @ {API_URL}")
    print(f"并行Worker数: {WORKERS}{'（DRY-RUN）' if DRY_RUN else ''}")

    if not os.path.isdir(SKILL_DIR):
        print(f"错误: skill目录不存在: {SKILL_DIR}")
        sys.exit(1)

    cases, skipped, overview_table = load_cases(CASES_PATH)
    groups = group_cases_by_fault_type(cases)
    print(f"\n有效案例 {len(cases)} 条，归为 {len(groups)} 个案例组；跳过 {len(skipped)} 条")
    for item in skipped:
        print(f"  - 跳过 第{item['slide']}页 {item['title']}：{item['reason']}")

    orphan_rows, orphan_cases = audit_overview_coverage(cases, skipped, overview_table)
    if orphan_rows:
        print(f"\n[WARN] 概览表有 {len(orphan_rows)} 行找不到可用的详情页（该根因蒸馏不出步骤）:")
        for definition, root_cause, note in orphan_rows:
            tail = f"，疑似对应被跳过的“{note}”" if note else ""
            print(f"  - {definition} / {root_cause}{tail}")
    if orphan_cases:
        print(f"\n[WARN] 有 {len(orphan_cases)} 条详情页未列入概览表（概览表可能漏行）:")
        for case in orphan_cases:
            print(f"  - {case_origin(case)} {case['fault_type']} / {case['root_cause']}")

    skill_index = build_skill_index(SKILL_DIR)
    print(f"\n既有skill {len(skill_index)} 个")
    skill_index_text = format_skill_index(skill_index)
    content_by_path = {item["rel_path"]: item["content"] for item in skill_index}

    # 人工指定过目标的案例组不再调模型定位
    pinned = [{"fault_type": ft, "target": sanitize_target(TARGET_OVERRIDES[ft]),
               "is_new": sanitize_target(TARGET_OVERRIDES[ft]) not in content_by_path,
               "reason": "人工指定（TARGET_OVERRIDES）"}
              for ft in groups if ft in TARGET_OVERRIDES]
    for loc in pinned:
        print(f"  {loc['fault_type']} → {loc['target']}（人工指定）")

    locate_args = [(fault_type, group_cases, skill_index_text, API_URL, MODEL_NAME)
                   for fault_type, group_cases in groups.items()
                   if fault_type not in TARGET_OVERRIDES]
    with Pool(processes=WORKERS) as pool:
        locations = pool.map(locate_group, locate_args)
    locations += pinned

    locate_errors = [loc for loc in locations if loc.get("error")]
    for loc in locate_errors:
        print(f"[WARN] 定位失败: {loc['fault_type']}: {loc['error']}")

    # 同一目标skill的多个案例组合成一次调用，既避免并行写同一文件，
    # 也让模型能一次看到该skill要新增的全部场景，减少重复小节。
    buckets = {}
    for loc in locations:
        if loc.get("error"):
            continue
        bucket = buckets.setdefault(loc["target"], {"fault_types": [], "cases": []})
        bucket["fault_types"].append(loc["fault_type"])
        bucket["cases"].extend(groups[loc["fault_type"]])
        print(f"  {loc['fault_type']} → {loc['target']}"
              f"{'（新建）' if loc['is_new'] else ''}：{loc.get('reason', '')}")

    # 目录清单要包含本次将新建的skill，跨分类引用才能解析
    catalog_paths = sorted(set(content_by_path) | set(buckets))
    skill_catalog = "\n".join(f"- [{p}]" for p in catalog_paths)

    merge_args = []
    for target, bucket in sorted(buckets.items()):
        detail = "\n\n".join(
            format_case_detail(ft, groups[ft], overview_table) for ft in bucket["fault_types"])
        merge_args.append((target, bucket["fault_types"], detail,
                           content_by_path.get(target), skill_catalog, API_URL, MODEL_NAME))

    with Pool(processes=WORKERS) as pool:
        results = pool.map(merge_bucket, merge_args)

    print("\n" + "=" * 60)
    for result in results:
        if result.get("error"):
            print(f"[FAIL] {result['target']}: {result['error']}")
            continue
        invalid = validate_change(result)
        if invalid:
            result["invalid"] = invalid
            print(f"[FAIL] {result['target']}: 生成内容不合规: {invalid}")
            continue
        if result["action"] == "covered":
            print(f"[SKIP] {result['target']}: 已被既有skill覆盖")
            continue
        apply_change(result, SKILL_DIR, DRY_RUN)

    report = build_report(results, skipped, locate_errors, CASES_PATH, SKILL_DIR, DRY_RUN)
    os.makedirs(os.path.dirname(os.path.abspath(REPORT_PATH)), exist_ok=True)
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n变更说明已保存到: {REPORT_PATH}")

    failed = [r for r in results if r.get("error") or r.get("invalid")]
    if failed or locate_errors:
        print(f"\n存在 {len(failed) + len(locate_errors)} 个处理失败项，请查看变更说明后重跑")
        sys.exit(1)


if __name__ == "__main__":
    main(
        CASES_PATH="cases/故障补充场景.json",
        SKILL_DIR="skills_distilled/07-16",
        API_URL="http://76.64.185.52:2207/v1/chat/completions",
        MODEL_NAME="qwen3.6-27b",
        WORKERS=3,
        # 变更说明写在skill目录外，避免被validate_skills.py当成skill校验
        REPORT_PATH=f"reports/skill_change_report_{datetime.now().strftime('%m-%d')}.md",
        # 追加不是幂等操作，重跑会重复追加。确认报告无误后再改False落盘，
        # 落盘前先备份或提交skill目录。
        DRY_RUN=True,
    )
