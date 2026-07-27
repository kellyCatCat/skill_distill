#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已经人工审过的变更说明落盘到skill库。

`skill_case_merge_pipeline.py` 的 DRY-RUN 只出报告不写文件，但改成
DRY_RUN=False 重跑会让模型重新生成一遍——落盘的内容就不是你审过的那一份了。
本脚本直接读 `reports/skill_change_report_<mm-dd>.md`，把其中"改动内容"
折叠块里的markdown应用到skill目录：追加块拼到文件末尾，新建块写成新文件。

落盘是幂等的：追加前先比对小节标题，已经存在的小节跳过，所以同一份报告
重复执行不会把内容追加两遍。

用法：
  python3 apply_change_report.py                       # 预演今天的报告，不写文件
  python3 apply_change_report.py --apply               # 实际写入
  python3 apply_change_report.py <报告路径> <skill目录> [--apply]
"""
import os
import re
import sys
from datetime import datetime

from skill_case_merge_pipeline import check_generated_content, section_headings

# 报告里每处改动的标题行，如：## 追加小节：`故障处理：IP路由/BGP故障案例.md`
CHANGE_HEADING = re.compile(r"^## (追加小节|新建skill)：`([^`]+)`\s*$", re.MULTILINE)
# 改动内容折叠块用四个反引号包裹，内部的三反引号代码块因此不会提前闭合
CONTENT_BLOCK = re.compile(r"````markdown\s*\n(.*?)\n````", re.DOTALL)

ACTION_BY_LABEL = {"追加小节": "append", "新建skill": "create"}


def parse_report(report_path: str) -> list:
    """从变更说明里解析出 [{action, target, content}]，按报告中的顺序返回。"""
    with open(report_path, 'r', encoding='utf-8') as f:
        report = f.read()

    changes = []
    matches = list(CHANGE_HEADING.finditer(report))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(report)
        block = CONTENT_BLOCK.search(report, match.end(), end)
        if not block:
            print(f"[WARN] {match.group(2)} 没有找到改动内容块，跳过")
            continue
        changes.append({
            "action": ACTION_BY_LABEL[match.group(1)],
            "target": match.group(2),
            "content": block.group(1).strip(),
        })
    return changes


def apply_one(change: dict, skill_dir: str, apply: bool) -> str:
    """应用一处改动，返回结果说明。"""
    action, target, content = change["action"], change["target"], change["content"]
    path = os.path.join(skill_dir, *target.split("/"))

    if action == "create":
        if os.path.exists(path):
            return f"[SKIP] 文件已存在，未覆盖: {target}"
        error = check_generated_content("create", content)
        if error:
            return f"[FAIL] {target}: {error}"
        if apply:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content + "\n")
        return f"[{'新建' if apply else '将新建'}] {target}"

    if not os.path.isfile(path):
        return f"[FAIL] 追加的目标文件不存在: {target}"
    with open(path, 'r', encoding='utf-8') as f:
        existing = f.read()

    # 幂等保护先于内容校验：小节已在文件中说明这份报告已经落过盘，属于正常重复
    # 执行，不该报失败（否则同名小节会被内容校验判成FAIL）。
    already = [h for h in section_headings(content) if h in set(section_headings(existing))]
    if already:
        return f"[SKIP] 小节已存在，未重复追加: {target}（{'、'.join(already)}）"

    error = check_generated_content("append", content, section_headings(existing), target)
    if error:
        return f"[FAIL] {target}: {error}"

    if apply:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f"{existing.rstrip()}\n\n{content}\n")
    return (f"[{'追加' if apply else '将追加'}] {target}"
            f"（{'、'.join(section_headings(content))}）")


def main(report_path: str, skill_dir: str, apply: bool):
    print("=" * 60)
    print("按变更说明落盘" + ("" if apply else "（预演，不写文件）"))
    print("=" * 60)
    print(f"变更说明: {report_path}")
    print(f"skill目录: {skill_dir}\n")

    if not os.path.isfile(report_path):
        print(f"错误: 变更说明不存在: {report_path}")
        sys.exit(1)
    if not os.path.isdir(skill_dir):
        print(f"错误: skill目录不存在: {skill_dir}")
        sys.exit(1)

    changes = parse_report(report_path)
    if not changes:
        print("错误: 变更说明里没有解析到任何改动内容")
        sys.exit(1)

    results = [apply_one(c, skill_dir, apply) for c in changes]
    for line in results:
        print(line)

    failed = [r for r in results if r.startswith("[FAIL]")]
    print(f"\n共 {len(changes)} 处改动，失败 {len(failed)} 处")
    if not apply:
        print("这是预演；确认无误后加 --apply 实际写入。")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply_flag = "--apply" in sys.argv[1:]
    main(
        args[0] if args else f"reports/skill_change_report_{datetime.now().strftime('%m-%d')}.md",
        args[1] if len(args) > 1 else "skills_distilled/07-16",
        apply_flag,
    )
