#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已经人工审过的变更说明落盘到skill库。

`skill_case_merge_pipeline.py` 和 `skill_eval_optimize_pipeline.py` 的 DRY-RUN
只出报告不写文件，但改成 DRY_RUN=False 重跑会让模型重新生成一遍——落盘的内容
就不是你审过的那一份了。本脚本直接读 `reports/skill_change_report_<mm-dd>.md`
或 `reports/skill_optimize_report_<mm-dd>.md`，把其中"改动内容"折叠块里的
markdown应用到skill目录：追加块拼到文件末尾，新建块写成新文件，优化块整篇覆盖
原文件。

落盘是幂等的：追加前先比对小节标题，已经存在的小节跳过；整篇覆盖前先比对内容，
与现有文件一致时跳过。所以同一份报告重复执行不会把内容追加两遍。

落盘时可以改文件名：报告里的路径是流水线按告警名推出来的，审的时候常常想换个
名字（几张表的告警名一样，推出来的路径就撞在一起），用 `--rename 旧=新` 指定，
不必回头去改报告或重跑流水线。

用法：
  python3 apply_change_report.py                       # 预演今天的报告，不写文件
  python3 apply_change_report.py --diff                # 逐行看落盘前后的差异
  python3 apply_change_report.py --apply               # 实际写入
  python3 apply_change_report.py <报告路径> <skill目录> [--diff|--apply]
  python3 apply_change_report.py <报告> <目录> --rename "排障步骤/CPU利用率超限定位.md=CPU利用率超限-NETCONF.md" --apply

`--rename` 可以给多次。等号左边写报告里的那个路径（只写文件名也行），右边写想要的
名字：不带 `/` 就只换文件名、目录不动，带 `/` 就整条路径都换；`.md` 可以省。
左边匹配不到任何一处改动时直接报错退出——拼错了却静默落到原名下，比不落盘更糟。

整篇覆盖只报"9609→9956字符"看不出改了什么，审的时候用 --diff 逐行看：判据有没有
真被改掉、原有场景有没有被顺手删掉。--diff 一定不写文件。
"""
import difflib
import os
import re
import sys
from datetime import datetime

from skill_case_merge_pipeline import check_generated_content, section_headings
from skill_eval_optimize_pipeline import check_optimized_content

# 报告里每处改动的标题行，如：## 追加小节：`故障处理：IP路由/BGP故障案例.md`
CHANGE_HEADING = re.compile(
    r"^## (追加小节|新建skill|优化skill)：`([^`]+)`\s*$", re.MULTILINE)
# 改动内容折叠块用四个反引号包裹，内部的三反引号代码块因此不会提前闭合
CONTENT_BLOCK = re.compile(r"````markdown\s*\n(.*?)\n````", re.DOTALL)

ACTION_BY_LABEL = {"追加小节": "append", "新建skill": "create",
                   "优化skill": "rewrite"}


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


def parse_renames(argv: list) -> list:
    """把命令行里的 `--rename 旧=新` 收成 [(旧, 新)]。`--rename=旧=新` 也认。"""
    pairs, i = [], 0
    while i < len(argv):
        arg = argv[i]
        value = ""
        if arg == "--rename" and i + 1 < len(argv):
            value, i = argv[i + 1], i + 1
        elif arg.startswith("--rename="):
            value = arg[len("--rename="):]
        if value:
            if "=" not in value:
                print(f"错误: --rename 要写成 `旧路径=新名字`，收到的是 {value!r}")
                sys.exit(2)
            old, new = value.split("=", 1)
            pairs.append((old.strip(), new.strip()))
        i += 1
    return pairs


def resolve_rename(target: str, new: str) -> str:
    """按 `--rename` 的右值算出新的相对路径。

    不带 `/` 就只换文件名、目录不动（"改写输出路径的文件名"通常就是这个意思）；
    带 `/` 就整条路径都换。`.md` 省了自动补上。
    """
    new = new.strip()
    # 落盘路径要拼到 skill 目录下：`..` 会写到目录外面去，而绝对路径去掉开头的
    # `/` 之后会变成一个看着差不多、其实不是你要的相对路径，两种都当错处理
    if os.path.isabs(new) or ".." in new.split("/"):
        print(f"错误: --rename 的新名字要写成 skill 目录下的相对路径"
              f"（不能是绝对路径、也不能带 `..`）: {new!r}")
        sys.exit(2)
    new = new.strip("/")
    if not new.endswith(".md"):
        new += ".md"
    if "/" in new:
        return new
    parent = "/".join(target.split("/")[:-1])
    return f"{parent}/{new}" if parent else new


def apply_renames(changes: list, renames: list) -> list:
    """按 `--rename` 改写各处改动的落盘路径，返回 [(原路径, 新路径)] 供打印。

    左值先按整条路径比，再按文件名比。一条都没匹配上就退出：拼错了却静默落到
    原名下，比不落盘更糟——人会以为自己改了名。
    """
    renamed = []
    for old, new in renames:
        old = old.strip().strip("/")
        hit = [c for c in changes
               if c["target"] == old or c["target"].split("/")[-1] == old
               or c["target"].split("/")[-1] == (old if old.endswith(".md") else old + ".md")]
        if not hit:
            print(f"错误: --rename 的 {old!r} 在报告里找不到对应的改动；"
                  f"报告里有：{'、'.join(c['target'] for c in changes)}")
            sys.exit(2)
        for change in hit:
            target = resolve_rename(change["target"], new)
            if target != change["target"]:
                renamed.append((change["target"], target))
                change["target"] = target
    return renamed


def render_diff(target: str, existing: str, new_text: str) -> str:
    """落盘前后的unified diff。

    整篇覆盖时只报"9609→9956字符"看不出改了什么，而这恰恰是审报告时最需要看的
    ——判据有没有真的被改掉、原有场景有没有被顺手删掉，都得逐行看。
    """
    diff = difflib.unified_diff(
        existing.splitlines(), new_text.splitlines(),
        fromfile=f"a/{target}（现有）", tofile=f"b/{target}（落盘后）", lineterm="")
    lines = list(diff)
    if not lines:
        return "    （无差异）"
    return "\n".join(f"    {line}" for line in lines)


def apply_one(change: dict, skill_dir: str, apply: bool, show_diff: bool = False) -> str:
    """应用一处改动，返回结果说明。show_diff 时附带unified diff且一定不写文件。"""
    action, target, content = change["action"], change["target"], change["content"]
    path = os.path.join(skill_dir, *target.split("/"))

    if action == "create":
        if os.path.exists(path):
            return f"[SKIP] 文件已存在，未覆盖: {target}"
        error = check_generated_content("create", content)
        if error:
            return f"[FAIL] {target}: {error}"
        if show_diff:
            return (f"[将新建] {target}（{len(content)}字符）\n"
                    + render_diff(target, "", content))
        if apply:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content + "\n")
        return f"[{'新建' if apply else '将新建'}] {target}"

    if not os.path.isfile(path):
        return f"[FAIL] {'覆盖' if action == 'rewrite' else '追加'}的目标文件不存在: {target}"
    with open(path, 'r', encoding='utf-8') as f:
        existing = f.read()

    if action == "rewrite":
        # 幂等保护先于内容校验：内容与现有文件一致说明这份报告已经落过盘
        if content.strip() == existing.strip():
            return f"[SKIP] 内容与现有文件一致，无需覆盖: {target}"
        error = check_optimized_content(content, existing, target)
        if error:
            return f"[FAIL] {target}: {error}"
        summary = f"{target}（{len(existing.strip())}→{len(content)}字符）"
        if show_diff:
            return f"[将覆盖] {summary}\n" + render_diff(target, existing.strip(), content)
        if apply:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content + "\n")
        return f"[{'已覆盖' if apply else '将覆盖'}] {summary}"

    # 幂等保护先于内容校验：小节已在文件中说明这份报告已经落过盘，属于正常重复
    # 执行，不该报失败（否则同名小节会被内容校验判成FAIL）。
    already = [h for h in section_headings(content) if h in set(section_headings(existing))]
    if already:
        return f"[SKIP] 小节已存在，未重复追加: {target}（{'、'.join(already)}）"

    error = check_generated_content("append", content, section_headings(existing), target)
    if error:
        return f"[FAIL] {target}: {error}"

    merged = f"{existing.rstrip()}\n\n{content}"
    if show_diff:
        return (f"[将追加] {target}（{'、'.join(section_headings(content))}）\n"
                + render_diff(target, existing.rstrip(), merged))
    if apply:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(merged + "\n")
    return (f"[{'追加' if apply else '将追加'}] {target}"
            f"（{'、'.join(section_headings(content))}）")


def main(report_path: str, skill_dir: str, apply: bool, show_diff: bool = False,
         renames: list = None):
    if show_diff:
        apply = False   # --diff 是审阅用的，永远不写文件
    print("=" * 60)
    print("按变更说明落盘"
          + ("（只看diff，不写文件）" if show_diff else "" if apply else "（预演，不写文件）"))
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

    for old_path, new_path in apply_renames(changes, renames or []):
        print(f"改名: {old_path} → {new_path}")
    if renames:
        print()

    results = [apply_one(c, skill_dir, apply, show_diff) for c in changes]
    for line in results:
        print(line)

    failed = [r for r in results if r.startswith("[FAIL]")]
    print(f"\n共 {len(changes)} 处改动，失败 {len(failed)} 处")
    if show_diff:
        print("这是diff预览；确认无误后加 --apply 实际写入。")
    elif not apply:
        print("这是预演；确认无误后加 --apply 实际写入。")
    if failed:
        sys.exit(1)


def _positional(argv: list) -> list:
    """去掉开关和 `--rename` 的取值，剩下的才是报告路径和 skill 目录。"""
    args, skip = [], False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg == "--rename":
            skip = True
            continue
        if arg.startswith("-"):
            continue
        args.append(arg)
    return args


if __name__ == "__main__":
    argv = sys.argv[1:]
    args = _positional(argv)
    main(
        args[0] if args else f"reports/skill_change_report_{datetime.now().strftime('%m-%d')}.md",
        args[1] if len(args) > 1 else "skills_distilled/07-16",
        "--apply" in argv,
        "--diff" in argv,
        parse_renames(argv),
    )
