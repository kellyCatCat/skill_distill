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

# 与主蒸馏流水线保持一致的写作约束，追加/新建的内容必须同样遵守
WRITING_RULES = """- 输出面向网管agent执行，凡是收集信息、联系技术支持、提交给工程师这类动作，整个步骤删除，并删除其它步骤对它的引用。
- 输出全文禁止出现"联系技术支持"、"寻求技术支持"、"提交给工程师"、"收集信息并联系"等表述，排障步骤穷尽后直接结束。
- 案例中形如"—— 待确认xxx"、"请刘瑞xxx"、"求助设备专家"、人名工号等内部讨论备注，一律不要写进skill。
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
- append：目标skill已存在，但新案例带来了它没有的根因、判据、命令、修复动作或管控接口。此时只输出需要追加的新小节，不要重复skill里已有的内容，也不要重写整篇skill。
- create：目标skill不存在（下方"目标skill"为"（不存在，需要新建）"），需要按新案例写一篇完整的skill。

# 内容要求
<writing_rules>
- append时：输出一个或多个以"## "开头的小节，小节标题包含根因名称（如"## 场景：ISIS System ID冲突"）；不要输出frontmatter（--- name/description ---）；步骤编号在小节内部从1开始。
- create时：输出完整skill，以frontmatter开头（name为英文小写+连字符，description为一句话简介），正文以一级标题"# "开始。

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


def _merge_extractor(text: str) -> str:
    try:
        decision = extract_json_block(text)
    except (ValueError, json.JSONDecodeError):
        return ""
    action = decision.get("action")
    if action == "covered":
        return text
    if action in ("append", "create") and extract_markdown_content(text):
        return text
    return ""


def normalize_case(entry: dict) -> dict:
    """把一条案例条目规整为统一字段；仅有标题的补充页返回None。"""
    if entry.get("type") == "supplementary" or not entry.get("故障类型"):
        return None
    return {
        "slide": entry.get("slide"),
        "fault_type": entry.get("故障类型", "").strip(),
        "root_cause": entry.get("根因类型", "").strip(),
        "alarm": entry.get("触发告警", "").strip(),
        "diagnosis": entry.get("故障诊断", "").strip(),
        "solution": entry.get("方案生成", "").strip(),
        "dependency": entry.get("对管控依赖", "").strip(),
        "note": entry.get("备注", "").strip(),
    }


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
            f"## 案例：{case['fault_type']} —— {case['root_cause']}（来源第{case['slide']}页）\n"
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
    reply = call_model_with_retry(api_url, model_name, prompt, extractor=_locate_extractor)
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
    reply = call_model_with_retry(api_url, model_name, prompt, extractor=_merge_extractor)
    result = {"target": target, "fault_types": fault_types, "is_new": not target_content}
    if reply.startswith("错误："):
        result["error"] = reply
        return result
    try:
        decision = extract_json_block(reply)
    except (ValueError, json.JSONDecodeError) as e:
        result["error"] = f"错误：合并结果解析失败: {e}"
        return result

    result["action"] = decision.get("action")
    result["reason"] = decision.get("reason", "")
    result["change_summary"] = decision.get("change_summary", "")
    result["sections"] = decision.get("sections", [])
    result["content"] = extract_markdown_content(reply)
    return result


def validate_change(result: dict) -> str:
    """应用前检查生成内容，返回错误说明（空串表示通过）。"""
    action, content = result.get("action"), (result.get("content") or "").strip()
    if action == "covered":
        return ""
    if not content:
        return f"action={action} 但没有生成内容"
    if action == "append":
        if content.startswith("---"):
            return "追加内容里带了frontmatter，应只输出小节"
        if not re.search(r"^## +\S", content, re.MULTILINE):
            return "追加内容里没有以'## '开头的小节"
        return ""
    if action == "create":
        if not re.match(r"^---\s*\n", content):
            return "新建skill缺少frontmatter"
        return ""
    return f"未知的action: {action!r}"


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
            print(f"  - 第{case['slide']}页 {case['fault_type']} / {case['root_cause']}")

    skill_index = build_skill_index(SKILL_DIR)
    print(f"\n既有skill {len(skill_index)} 个")
    skill_index_text = format_skill_index(skill_index)
    content_by_path = {item["rel_path"]: item["content"] for item in skill_index}

    locate_args = [(fault_type, group_cases, skill_index_text, API_URL, MODEL_NAME)
                   for fault_type, group_cases in groups.items()]
    with Pool(processes=WORKERS) as pool:
        locations = pool.map(locate_group, locate_args)

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
        SKILL_DIR=f"skills_distilled/{datetime.now().strftime('%m-%d')}",
        API_URL="http://76.64.185.52:2207/v1/chat/completions",
        MODEL_NAME="qwen3.6-27b",
        WORKERS=3,
        # 变更说明写在skill目录外，避免被validate_skills.py当成skill校验
        REPORT_PATH=f"reports/skill_change_report_{datetime.now().strftime('%m-%d')}.md",
        DRY_RUN=False,
    )
