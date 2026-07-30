#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根据排障评测结果优化既有skill。

评测是让排障agent照着skill实跑注入的故障，人工判断诊断结论对不对。判"诊断失败"
时，往往不是agent的问题而是skill的判据有缺陷——比如某一步只凭本端配置就下结论、
某条判据会让agent提前放行、agent实测到的异常现象在skill里没有对应的排查落点。
本流水线把评测结论连同skill全文交给模型，定位是哪一步误导了agent并改掉。

流程：
  1. 读评测结果（evals/*.md|*.txt，自然语言）：按"对应SKILL：<路径>"标记切分成若干条
     评测记录，同一篇skill的多条评测合并成一次调用，让模型一次看到全部问题；
  2. 定位：把评测里写的skill路径匹配到skill库中真实存在的文件（评测常省略
     "故障处理："这类一级目录前缀，所以允许一级目录后缀与文件名匹配，歧义时报错）；
  3. 优化：把skill全文与评测结论交给模型，产出整篇优化后的skill（改判据要动既有正文，
     不是追加小节，所以只能整篇重写）；
  4. 报告：输出 reports/skill_optimize_report_<mm-dd>.md，人工审过后用
     apply_change_report.py 落盘。

评测里的"对应SKILL："写skill库中的相对路径，允许省略"故障处理："这类一级目录前缀
（写"IP路由/BGP故障案例.md"能匹配到"故障处理：IP路由/BGP故障案例.md"）。匹配不唯一
或找不到时脚本报错并给出最接近的候选，不会挑一个最像的去覆盖。

用法：
  python3 skill_eval_optimize_pipeline.py --check    # 只看评测匹配到哪篇skill，不调模型
  python3 skill_eval_optimize_pipeline.py            # 按文件末尾main()的默认参数运行
"""
import json
import os
import re
import time
import sys
from datetime import datetime
from multiprocessing import Pool

from model_config import resolve_model
from skill_self_distill_pipeline import (_longest_common_substring_len,
                                         _strip_generic_tokens,
                                         call_model_with_retry, extract_markdown_content)
from skill_case_merge_pipeline import (BANNED_CONTENT_PATTERNS, WRITING_RULES,
                                       extract_json_block, section_headings)

# 一条评测记录的起始标记，如：对应SKILL: IP路由/BGP故障案例.md
EVAL_RECORD_HEADING = re.compile(
    r"^\s*(?:对应|目标)?\s*(?:SKILL|skill|Skill)\s*[：:]\s*([^\r\n]+?\.md)\s*$", re.MULTILINE)

# 评测里的skill路径匹配不到、或匹配到多个时，在这里人工指定。
# 键为评测中写的路径，值为skill库中的真实相对路径。
EVAL_TARGET_OVERRIDES = {}

# 整篇重写时，篇幅或小节数缩水到原文这个比例以下就判失败：优化可以合并重复小节，
# 但缩掉近半说明模型没写完或用"其余保持不变"偷懒，不能直接落盘。
MIN_KEEP_RATIO = 0.6

OPTIMIZE_PROMPT_TEMPLATE = """你是IPRAN网络运维专家，正在维护一套供网管agent使用的排障skill库。下面这篇skill在评测中暴露了问题：评测是让排障agent照着这篇skill实跑注入的故障，再由人工判断诊断结论对不对。请找出是skill的哪些内容导致了评测中的错误，改掉它们，输出优化后的完整skill。

# 待优化的skill：<target_path>
<target_skill>

# 评测结果
<eval_detail>

# skill目录清单
跨分类引用时必须使用清单中的路径，格式为[路径]：
<skill_catalog>

# 定位缺陷的方法
评测判"诊断失败/修复失败"时，先在skill正文里找到agent当时执行的是哪一步、它依据哪句话得出了错误结论，再判断属于下面哪类缺陷：

1. **单端判据**：检查项只看本端的配置或状态就下结论，而该故障本质是两端参数不一致（如本端配的对端AS号与对端设备实际的本地AS号不一致、两端会话标识符/Service ID/MTU不一致）。这类步骤必须改成两端比对，写清在对端执行什么命令、取哪个字段、和本端的哪个字段比。
2. **会提前放行的错误判据**：判据写得看似合理但会让agent误判为正常（例如"eBGP邻居AS号必须不同"——两端AS号不同却依然可能与对端实际AS号不匹配，agent照此判据会直接放行）。必须把判据改写成真正能区分正常与故障的条件，并删掉会误导的表述。
3. **缺失的排查落点**：评测中agent实测到了某个异常现象（如TCP处于LISTEN、Foreign Port为0、本端发出SYN未收到响应），但skill里没有对应的检查步骤，或没有说明该现象指向哪些根因，导致agent只能停在表层结论。必须补上该现象的检查步骤，并写清它可能对应的根因以及下一步查什么。
4. **顺序不合理**：能直接给出根因的检查（如协议错误码日志）排在大量低命中率检查之后，导致agent绕远路或提前收敛到错误结论。把高命中率、判据明确的步骤前移。
5. **同一根因散落重复**：同一个根因在多个场景各写一遍且判据不一致，agent会按先遇到的那份下结论。合并成一处，保留其中更完整的判据。

评测结论若反映的是agent自身没按skill执行（skill该写的都写了、判据也对），则判no-change并说明理由，不要为了改而改。

# 评测里若给了"正确的诊断流程"
评测材料除了失败记录，可能还附有这类故障的权威诊断流程（小节名如"正确的诊断流程"、"标准流程"，内容通常包含故障类型、触发告警、逐步的诊断判据、方案生成、配置样例、对管控的依赖）。给了就以它为准：

- **按它校准既有正文**：skill 里与之冲突的判据要改掉；它有而 skill 没有的诊断步骤、判据、修复动作、管控接口、配置样例要补进来；步骤顺序按它的先后组织。
- **跳转必须真的走得通**：流程往往是"第1步→第2步→第3步"的链条。改完后逐条检查 skill 里每处"转步骤X"、"参考场景Y"指向的小节，内容是不是真的是那一步该做的事——指错地方等于把排查链在这里截断，agent 走到死胡同就只能自己发挥，这正是评测失败的常见成因。
- **告警名要写进来**，agent 是从告警进入排障的，流程里列出的告警名必须能在 skill 里检索到。
- **场景约束照写**（如"只支持公网面"、"对端也在管理范围内"），但仍遵守上面的写作约束：流程标了"NA，人工远程修复"的根因，skill 里只写到根因判定为止，不要编修复步骤；流程里形如"求助设备专家对一下"、"需补充约束"、"待确认"这类内部讨论备注一律不写进skill。
- 流程与失败记录冲突时以流程为准；失败记录用来定位 skill 现在错在哪一步。

# 内容要求
<writing_rules>
- 输出整篇优化后的skill全文，以frontmatter开头（name为英文小写+连字符），正文以一级标题"# "开始。
- 严禁用"（其余步骤保持不变）"、"（此处省略）"、"同上"之类的省略写法代替正文——落盘时会整篇覆盖原文件，省略掉的部分就真的丢了。
- 除评测暴露的问题以及为此必需的结构调整外，原skill覆盖的故障场景一个都不能少；合并重复小节是允许的，整段删掉未被评测质疑的场景是不允许的。
- 修改后的步骤编号要连续、场景之间的跳转指引（"转场景X"）必须仍然指向真实存在的小节。
- 同一篇skill内的互相引用写成"参考本文场景X"，不要用[路径.md]引用这篇skill自己。

# 输出格式
先输出一个json代码块给出判定，再输出一个markdown代码块给出优化后的skill全文（action为no-change时不输出markdown代码块）：
```json
{"action": "optimized|no-change", "reason": "一句话说明判定理由", "change_summary": "一句话概括本次改动", "fixes": [{"defect": "缺陷类型", "location": "原skill中的位置，如公共前置检查步骤3", "fix": "改成了什么"}]}
```
```markdown
（优化后的skill全文）
```
"""


HEADING_LINE = re.compile(r"^#{1,6} +\S")


def _leading_headings(text_before: str, limit: int = 3) -> tuple:
    """紧邻 marker 之前的标题行，返回 (标题列表, 这些标题的起始位置)。

    评测常写成"## 故障类型 / ### 根因 / 对应SKILL："这种层级，标题在marker之前，
    直接按marker切会把它们丢掉（一个文件放多条记录时，就分不清哪条对哪个根因）。
    """
    lines = text_before.splitlines(keepends=True)
    taken, cut, idx = [], len(text_before), len(lines)
    while idx > 0:
        line = lines[idx - 1]
        if not line.strip():
            idx, cut = idx - 1, cut - len(line)
            continue
        if HEADING_LINE.match(line) and len(taken) < limit:
            taken.insert(0, line.strip())
            idx, cut = idx - 1, cut - len(line)
            continue
        break
    return taken, cut


def parse_eval_text(text: str, source: str) -> list:
    """把一份评测结果切分成 [{raw_target, detail, source}]。

    以"对应SKILL：<路径>"作为一条记录的起始，正文取到下一条记录的标题或文件末尾；
    marker 之前的标题行归到这条记录的开头当上下文。
    """
    records = []
    matches = list(EVAL_RECORD_HEADING.finditer(text))
    if not matches:
        return records
    for i, match in enumerate(matches):
        context, _ = _leading_headings(text[:match.start()])
        if i + 1 < len(matches):
            # 下一条记录的标题不算本条的正文
            _, end = _leading_headings(text[:matches[i + 1].start()])
        else:
            end = len(text)
        detail = text[match.end():end].strip()
        if context:
            detail = "\n".join(context) + "\n\n" + detail
        records.append({
            "raw_target": match.group(1).strip(),
            "detail": detail,
            "source": source,
        })
    return records


def load_evals(evals_path: str) -> tuple:
    """读评测结果（单个文件或目录），返回 (记录列表, 无法解析的文件列表)。"""
    if os.path.isdir(evals_path):
        files = sorted(
            os.path.join(root, name)
            for root, dirs, names in os.walk(evals_path)
            for name in names
            if name.endswith((".md", ".txt"))
        )
    else:
        files = [evals_path]

    records, unparsed = [], []
    for path in files:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
        found = parse_eval_text(text, os.path.relpath(path, os.path.dirname(evals_path) or "."))
        if not found:
            unparsed.append(path)
            continue
        records += found
    return records, unparsed


def suggest_candidates(rel: str, known_paths: set, limit: int = 3) -> list:
    """按文件名相似度给出最接近的几个skill，只用于匹配失败时的提示。

    刻意不拿它自动匹配：优化是整篇覆盖，匹配错就把另一篇skill的正文冲掉了，
    所以拿不准时必须报错让人来定，而不是挑一个最像的。
    """
    base = _strip_generic_tokens(rel.split("/")[-1][:-3])
    scored = []
    for path in known_paths:
        score = _longest_common_substring_len(
            base, _strip_generic_tokens(path.split("/")[-1][:-3]))
        if score >= 2:
            scored.append((score, path))
    return [p for _, p in sorted(scored, key=lambda x: (-x[0], x[1]))[:limit]]


def _with_suggestions(message: str, rel: str, known_paths: set) -> str:
    hits = suggest_candidates(rel, known_paths)
    if not hits:
        return message
    return (f"{message}；最接近的候选: {'、'.join(hits)}"
            f"（确认后写进评测的“对应SKILL：”行，或加进 EVAL_TARGET_OVERRIDES）")


def resolve_skill_path(raw_target: str, known_paths: set) -> tuple:
    """把评测里写的skill路径匹配到skill库中的真实路径，返回 (真实路径, 匹配方式)。

    评测里常省略一级目录的"故障处理："前缀（写成"IP路由/BGP故障案例.md"，
    真实路径是"故障处理：IP路由/BGP故障案例.md"），所以精确匹配之外还允许按
    一级目录后缀和文件名匹配；匹配到多个候选时抛异常，交人工用
    EVAL_TARGET_OVERRIDES 指定，不猜。
    """
    if raw_target in EVAL_TARGET_OVERRIDES:
        pinned = EVAL_TARGET_OVERRIDES[raw_target]
        if pinned not in known_paths:
            raise ValueError(f"EVAL_TARGET_OVERRIDES 指定的路径不存在: {pinned!r}")
        return pinned, "人工指定（EVAL_TARGET_OVERRIDES）"

    rel = raw_target.strip().strip("[]`").replace("\\", "/").lstrip("./")
    if not rel.endswith(".md"):
        raise ValueError(f"评测里的skill路径不是.md文件: {raw_target!r}")
    if rel in known_paths:
        return rel, "精确匹配"

    def split(path):
        parts = path.split("/")
        return (parts[0], parts[1]) if len(parts) == 2 else ("", parts[-1])

    eval_lvl1, eval_base = split(rel)
    if eval_lvl1:
        # 一级目录被省略了前缀（或反之），文件名必须完全一致
        hits = sorted({
            p for p in known_paths
            if split(p)[1] == eval_base and split(p)[0]
            and (split(p)[0].endswith(eval_lvl1) or eval_lvl1.endswith(split(p)[0]))
        })
        if len(hits) == 1:
            return hits[0], f"按一级目录后缀匹配（评测写的是{rel}）"
        if len(hits) > 1:
            raise ValueError(f"路径 {raw_target!r} 匹配到多个skill: {'、'.join(hits)}，"
                             f"请在 EVAL_TARGET_OVERRIDES 中指定")

    hits = sorted({p for p in known_paths if split(p)[1] == eval_base})
    if len(hits) == 1:
        return hits[0], f"按文件名匹配（评测写的是{rel}）"
    if len(hits) > 1:
        raise ValueError(f"路径 {raw_target!r} 匹配到多个skill: {'、'.join(hits)}，"
                         f"请在 EVAL_TARGET_OVERRIDES 中指定")
    raise ValueError(_with_suggestions(
        f"在skill库中找不到 {raw_target!r} 对应的skill", rel, known_paths))


FRONTMATTER_PATTERN = re.compile(r"^---\s*\n.*?\n---\s*(?:\n|$)", re.DOTALL)


def restore_frontmatter(content: str, original: str) -> tuple:
    """模型整篇重写时经常只输出正文、把frontmatter丢掉。

    优化改的是正文里的判据，name/description本来就不需要动，所以缺失时直接把原文的
    frontmatter补回来——比整轮判失败重跑三次更合适（qwen连试三次都漏这一段）。
    模型自己输出了frontmatter时以它为准，不覆盖。返回 (内容, 是否补过)。
    """
    content = (content or "").strip()
    if not content or FRONTMATTER_PATTERN.match(content):
        return content, False
    match = FRONTMATTER_PATTERN.match((original or "").lstrip())
    if not match:
        return content, False
    return f"{match.group(0).strip()}\n\n{content}", True


def prepare_optimized_content(reply: str, original: str, target_path: str = "") -> tuple:
    """从回复里取出 (判定, 内容, 是否补过frontmatter, 错误说明)。"""
    decision = extract_json_block(reply)
    if decision.get("action") == "no-change":
        return decision, "", False, ""
    content, repaired = restore_frontmatter(extract_markdown_content(reply), original)
    return (decision, content, repaired,
            check_optimized_content(content, original, target_path))


def check_optimized_content(content: str, original: str, target_path: str = "") -> str:
    """检查整篇重写的结果是否可直接覆盖原文件，返回错误说明（空串表示通过）。"""
    content = (content or "").strip()
    if not content:
        return "action=optimized 但没有生成内容"
    if not re.match(r"^---\s*\n", content):
        return "优化后的skill缺少frontmatter"
    if not re.search(r"^# \S", content, re.MULTILINE):
        return "优化后的skill缺少一级标题（# xxx）"
    for pattern, advice in BANNED_CONTENT_PATTERNS:
        hit = re.search(pattern, content)
        if hit:
            return f"正文出现了'{hit.group(0)}'：{advice}"
    if target_path and re.search(
            r"\[[^\[\]\n]*" + re.escape(target_path.split("/")[-1]) + r"\]", content):
        return f"引用了这篇skill自身[{target_path}]，同一篇内应写成'参考本文场景X'"
    if len(re.findall(r"^\s*```", content, re.MULTILINE)) % 2 != 0:
        return "代码块围栏未闭合，疑似输出被截断"

    # 整篇覆盖会丢掉原文里没被重新输出的内容，所以缩水必须挡住
    original = (original or "").strip()
    if original:
        if len(content) < len(original) * MIN_KEEP_RATIO:
            return (f"篇幅缩到原文的{len(content) / len(original):.0%}"
                    f"（{len(original)}→{len(content)}字符），疑似没写完或用了省略写法")
        old_headings, new_headings = section_headings(original), section_headings(content)
        if old_headings and len(new_headings) < len(old_headings) * MIN_KEEP_RATIO:
            return (f"二级小节从{len(old_headings)}个减到{len(new_headings)}个，"
                    f"疑似整段丢失了原有场景")
    return ""


def make_optimize_extractor(original: str, target_path: str):
    """内容不合规时抛异常，让call_model_with_retry重试，并把原因带进最终报错。"""
    def _extractor(text: str) -> str:
        try:
            _, content, _, error = prepare_optimized_content(text, original, target_path)
        except (ValueError, json.JSONDecodeError) as e:
            raise ValueError(f"判定json解析失败: {e}")
        if error:
            raise ValueError(f"{error}；提取到的内容开头: {content[:120]!r}")
        return text
    return _extractor


def format_eval_detail(records: list) -> str:
    blocks = []
    for i, record in enumerate(records, 1):
        blocks.append(f"## 评测记录{i}（来源 {record['source']}）\n{record['detail']}")
    return "\n\n".join(blocks)


def optimize_skill(args: tuple) -> dict:
    (target, records, original, skill_catalog, api_url, model_name,
     max_tokens, timeout) = args
    eval_detail = format_eval_detail(records)
    prompt = (OPTIMIZE_PROMPT_TEMPLATE
              .replace("<target_path>", target)
              .replace("<target_skill>", original)
              .replace("<eval_detail>", eval_detail)
              .replace("<skill_catalog>", skill_catalog)
              .replace("<writing_rules>", WRITING_RULES))
    print(f"[PID {os.getpid()}] 优化: {target}（{len(records)} 条评测）")

    started = time.time()
    result = {"target": target, "eval_count": len(records), "model": model_name,
              "sources": sorted({r["source"] for r in records}),
              "original_chars": len(original.strip()),
              "original_sections": len(section_headings(original))}
    reply = call_model_with_retry(api_url, model_name, prompt,
                                  extractor=make_optimize_extractor(original, target),
                                  max_tokens=max_tokens, timeout=timeout)
    result["elapsed"] = round(time.time() - started, 1)
    if reply.startswith("错误："):
        result["error"] = reply
        return result
    try:
        decision, content, repaired, invalid = prepare_optimized_content(
            reply, original, target)
    except (ValueError, json.JSONDecodeError) as e:
        result["error"] = f"错误：优化结果解析失败: {e}"
        return result

    result["action"] = decision.get("action")
    result["reason"] = decision.get("reason", "")
    result["change_summary"] = decision.get("change_summary", "")
    result["fixes"] = decision.get("fixes", [])
    if result["action"] != "no-change":
        result["content"] = content
        result["invalid"] = invalid
        if repaired:
            result["repaired"] = "模型未输出frontmatter，已补回原skill的frontmatter"
    return result


def apply_optimization(result: dict, skill_dir: str, dry_run: bool) -> None:
    path = os.path.join(skill_dir, *result["target"].split("/"))
    if dry_run:
        print(f"[DRY-RUN] rewrite → {path}")
        return
    with open(path, 'w', encoding='utf-8') as f:
        f.write(result["content"].strip() + "\n")
    print(f"已覆盖: {path}")


def format_fixes(fixes) -> list:
    lines = []
    for fix in fixes or ():
        if isinstance(fix, dict):
            parts = [str(fix.get(k, "")).strip()
                     for k in ("defect", "location", "fix") if fix.get(k)]
            lines.append(f"  - {'｜'.join(parts)}" if parts else f"  - {fix}")
        else:
            lines.append(f"  - {fix}")
    return lines


def build_report(results: list, unresolved: list, unparsed: list,
                 evals_path: str, skill_dir: str, dry_run: bool) -> str:
    lines = [
        "# 按评测结果优化skill的变更说明",
        "",
        f"- 评测来源：`{evals_path}`",
        f"- skill目录：`{skill_dir}`",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if dry_run:
        lines.append("- 运行模式：DRY-RUN（未写入skill文件）")
    lines += ["", "## 变更总览", "",
              "| 目标skill | 动作 | 评测条数 | 说明 |",
              "| --- | --- | --- | --- |"]
    action_label = {"optimized": "优化skill", "no-change": "无需改动",
                    "failed": "处理失败"}
    for r in results:
        action = "failed" if r.get("error") or r.get("invalid") else r.get("action", "failed")
        note = (r.get("error") or r.get("invalid") or r.get("change_summary")
                or r.get("reason", ""))
        lines.append(f"| `{r['target']}` | {action_label.get(action, action)} | "
                     f"{r['eval_count']} | {note} |")

    failed = [r for r in results if r.get("error") or r.get("invalid")]
    if failed:
        lines += ["", "## 处理失败（未落盘，需重跑）", ""]
        for r in failed:
            lines += [f"### `{r['target']}`", "",
                      f"- 失败原因：{r.get('error') or r.get('invalid')}"]
            if r.get("content"):
                lines += ["", "<details><summary>模型生成的内容（供排查）</summary>", "",
                          "````markdown", r["content"].strip()[:4000], "````", "",
                          "</details>", ""]

    # 标题与折叠块的格式必须与 apply_change_report.py 的解析器一致
    for r in results:
        if r.get("action") != "optimized" or r.get("invalid") or r.get("error"):
            continue
        lines += ["", f"## 优化skill：`{r['target']}`", "",
                  f"- 评测来源：{'、'.join(r['sources'])}（{r['eval_count']} 条）",
                  # 落盘会整篇覆盖，所以匹配方式要写进报告：不是精确匹配时，
                  # 审的人得先确认覆盖的确实是这篇skill
                  f"- 匹配方式：{r.get('match', '')}",
                  f"- 判定理由：{r.get('reason', '')}",
                  f"- 改动概要：{r.get('change_summary', '')}"]
        if r.get("repaired"):
            lines.append(f"- 自动修复：{r['repaired']}")
        if r.get("fixes"):
            lines.append("- 逐条处置：")
            lines += format_fixes(r["fixes"])
        lines += ["", "<details><summary>改动内容</summary>", "", "````markdown",
                  r["content"].strip(), "````", "", "</details>"]

    unchanged = [r for r in results if r.get("action") == "no-change"]
    if unchanged:
        lines += ["", "## 判定为无需改动（skill本身没问题）", ""]
        for r in unchanged:
            lines.append(f"- `{r['target']}`：{r.get('reason', '')}")

    if unresolved:
        lines += ["", "## 无法定位到skill的评测记录", ""]
        for item in unresolved:
            lines.append(f"- {item['raw_target']}（来源 {item['source']}）：{item['error']}")

    if unparsed:
        lines += ["", "## 没解析出评测记录的文件", ""]
        for path in unparsed:
            lines.append(f"- `{path}`：缺少“对应SKILL：<路径>”标记")

    return "\n".join(lines) + "\n"


def main(EVALS_PATH, SKILL_DIR, API_URL, MODEL_NAME, WORKERS, REPORT_PATH,
         DRY_RUN=True, CHECK_ONLY=False, MAX_TOKENS=None, TIMEOUT=600,
         EXIT_ON_FAILURE=True):
    """EXIT_ON_FAILURE=False 时返回结果供 compare_models.py 汇总，不退出进程。"""
    print("=" * 60)
    print("按评测结果优化skill流水线")
    print("=" * 60)
    cfg = resolve_model(MODEL_NAME, API_URL)
    print(f"\n评测来源: {EVALS_PATH}")
    print(f"skill目录: {SKILL_DIR}")
    print(f"模型: {MODEL_NAME} @ {cfg['api_url']}"
          f"（thinking {'开' if cfg['thinking'] else '关'}，"
          f"max_tokens {MAX_TOKENS or cfg['max_tokens']}）")
    if not cfg["registered"]:
        print(f"[WARN] {MODEL_NAME} 未登记在 model_config.MODEL_PROFILES 里，"
              f"按不开思考、默认预算处理")
    print(f"并行Worker数: {WORKERS}{'（DRY-RUN）' if DRY_RUN else ''}")

    if not os.path.exists(EVALS_PATH):
        print(f"错误: 评测来源不存在: {EVALS_PATH}")
        sys.exit(1)
    if not os.path.isdir(SKILL_DIR):
        print(f"错误: skill目录不存在: {SKILL_DIR}")
        sys.exit(1)

    records, unparsed = load_evals(EVALS_PATH)
    print(f"\n解析出 {len(records)} 条评测记录")
    for path in unparsed:
        print(f"[WARN] 没解析出评测记录（缺少“对应SKILL：<路径>”标记）: {path}")
    if not records:
        print("错误: 没有解析到任何评测记录")
        sys.exit(1)

    known_paths = {
        os.path.relpath(os.path.join(root, name), SKILL_DIR).replace(os.sep, "/")
        for root, dirs, names in os.walk(SKILL_DIR)
        for name in names if name.endswith(".md")
    }
    print(f"既有skill {len(known_paths)} 个")

    # 同一篇skill的多条评测合成一次调用，模型才能一次看到这篇的全部问题
    print("\n评测记录 → skill 的匹配结果:")
    buckets, unresolved = {}, []
    for record in records:
        try:
            target, how = resolve_skill_path(record["raw_target"], known_paths)
        except ValueError as e:
            unresolved.append({**record, "error": str(e)})
            print(f"  [FAIL] {record['raw_target']}（来源 {record['source']}）: {e}")
            continue
        bucket = buckets.setdefault(target, {"records": [], "hows": []})
        bucket["records"].append(record)
        if how not in bucket["hows"]:
            bucket["hows"].append(how)
        print(f"  {record['raw_target']}（来源 {record['source']}） → {target}（{how}）")

    # 优化是整篇覆盖，匹配错就把另一篇skill冲掉了，所以提供 --check 先只看匹配结果，
    # 确认无误再花模型调用去跑优化。
    if CHECK_ONLY:
        print(f"\n--check：{len(buckets)} 篇skill待优化，"
              f"{len(unresolved)} 条评测未匹配到skill（未调用模型，未写任何文件）")
        sys.exit(1 if unresolved or not buckets else 0)

    if not buckets:
        print("错误: 没有一条评测记录能定位到skill")
        sys.exit(1)

    skill_catalog = "\n".join(f"- [{p}]" for p in sorted(known_paths))

    task_args = []
    for target, bucket in sorted(buckets.items()):
        with open(os.path.join(SKILL_DIR, *target.split("/")),
                  'r', encoding='utf-8', errors='replace') as f:
            original = f.read()
        task_args.append((target, bucket["records"], original, skill_catalog,
                          API_URL, MODEL_NAME, MAX_TOKENS, TIMEOUT))

    with Pool(processes=WORKERS) as pool:
        results = pool.map(optimize_skill, task_args)
    for result in results:
        result["match"] = "；".join(buckets[result["target"]]["hows"])

    print("\n" + "=" * 60)
    for result in results:
        if result.get("error"):
            print(f"[FAIL] {result['target']}: {result['error']}")
        elif result.get("invalid"):
            print(f"[FAIL] {result['target']}: 生成内容不合规: {result['invalid']}")
        elif result.get("action") == "no-change":
            print(f"[SKIP] {result['target']}: 判定为skill本身没问题")
        else:
            apply_optimization(result, SKILL_DIR, DRY_RUN)

    report = build_report(results, unresolved, unparsed, EVALS_PATH, SKILL_DIR, DRY_RUN)
    os.makedirs(os.path.dirname(os.path.abspath(REPORT_PATH)), exist_ok=True)
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n变更说明已保存到: {REPORT_PATH}")
    if DRY_RUN:
        print("这是DRY-RUN；审过报告后用 "
              f"python3 apply_change_report.py {REPORT_PATH} {SKILL_DIR} --apply 落盘。")

    failed = [r for r in results if r.get("error") or r.get("invalid")]
    if failed or unresolved:
        print(f"\n存在 {len(failed) + len(unresolved)} 个处理失败项，请查看变更说明后重跑")
        if EXIT_ON_FAILURE:
            sys.exit(1)
    return {"model": MODEL_NAME, "results": results, "unresolved": unresolved,
            "unparsed": unparsed, "report_path": REPORT_PATH}


if __name__ == "__main__":
    main(
        EVALS_PATH="evals",
        SKILL_DIR="skills_distilled/07-27",
        # 地址与密钥从 .env 按模型名解析（见 model_config.py），不必写死在这里；
        # 要临时指向别的部署时才传 API_URL。
        API_URL=None,
        # 这条流水线要做因果定位（评测里的哪一步判据误导了agent）再整篇重写skill。
        # 一度默认用 MiniMax-M2.7-thinking，但在 BGP AS号不匹配 那条评测上实测下来
        # 它最差：耗时是 qwen 的11.8倍，只给2条fix、且两条都依赖BGP错误码——而该
        # 评测的现场TCP都没建起来、根本不会产生NOTIFICATION，改完照样卡在原地；
        # 其中一条还把 Bad Peer AS 的 Error Code 写成了1（应为2）。
        # MiniMax-M2.7 覆盖最全（4条fix，含删除"eBGP AS号必须不同"这句会放行的判据）
        # 且错误码写对，192秒也可接受。换模型只需改这里。
        MODEL_NAME="MiniMax-M2.7",
        WORKERS=3,
        # 变更说明写在skill目录外，避免被validate_skills.py当成skill校验
        REPORT_PATH=f"reports/skill_optimize_report_{datetime.now().strftime('%m-%d')}.md",
        # 整篇覆盖会丢掉原文，确认报告无误后用 apply_change_report.py 落盘，
        # 而不是改这里重跑——重跑会让模型重新生成，落盘的就不是审过的那份。
        DRY_RUN=True,
        # 加 --check 只跑到匹配这一步：先确认每条评测落在哪篇skill上，再花模型调用
        CHECK_ONLY="--check" in sys.argv[1:],
        # 留空用该模型在 model_config 里的预算；整篇重写撞上限时在这里单独调大
        MAX_TOKENS=None,
        # 整篇重写一篇skill实测约200秒（MiniMax-M2.7）；留三倍余量。
        # 换成推理更重的模型要相应调大，否则会在生成完之前就超时。
        TIMEOUT=600,
    )
