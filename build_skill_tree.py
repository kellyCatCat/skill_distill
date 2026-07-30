#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重新生成 skill 目录的 skill_tree_structure.txt。

这份树结构原本是主蒸馏流水线的副产物，而且是按**源文档树**的分组写出来的，不是扫
skill 目录得来的。于是有两个问题：

  1. 后来由 `skill_case_merge_pipeline.py` 新建的 skill（action=create）不在里面，
     因为它们在源文档树里没有对应分组；
  2. 想刷新它就得重跑整条蒸馏流水线——既要源文档树在手，又要重花几十次模型调用，
     还会把现有 skill 全部覆盖一遍。

本脚本直接扫 skill 目录重新生成，不调模型、只写这一个文件，所以每次并入新 skill
之后都可以顺手跑一遍。输出格式与流水线完全一致（两边共用下面的 render_tree）。

用法：
  python3 build_skill_tree.py                    # 重新生成 skills_distilled/<今天mm-dd>
  python3 build_skill_tree.py <skill目录>
  python3 build_skill_tree.py <skill目录> --dry-run   # 只打印，不写文件
"""
import json
import os
import sys
from datetime import datetime

TREE_FILENAME = "skill_tree_structure.txt"


def render_tree(rel_paths, output_dir: str) -> str:
    """按 references 风格渲染 skill 树，可直接挂到总SKILL.md的 # references 下。

    rel_paths 为 skill 的相对路径（如"一级目录/二级目录.md"）。按一级目录归类，
    叶子写该路径除一级目录之外的剩余部分——所以嵌套更深的 skill 也不会被漏掉，
    叶子上会带出它的子路径。
    """
    listing_lines = ["# references", f"- {output_dir}"]
    seen_cats = set()
    for rel in sorted(rel_paths):
        parts = rel.split("/")
        if len(parts) == 1:
            listing_lines.append(f"  - {parts[0]}")
            continue
        level1, leaf = parts[0], "/".join(parts[1:])
        if level1 not in seen_cats:
            listing_lines.append(f"  - {level1}")
            seen_cats.add(level1)
        listing_lines.append(f"    - {leaf}")
    return "\n".join(listing_lines)


def write_tree_file(output_dir: str, tree_text: str) -> str:
    """写 skill_tree_structure.txt，返回文件路径。"""
    tree_output_path = os.path.join(output_dir, TREE_FILENAME)
    with open(tree_output_path, 'w', encoding='utf-8') as f:
        f.write("Skill树结构图\n")
        f.write(f"输出目录: {output_dir}\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("引用格式: [一级目录/二级目录.md]，agent直接Read该相对路径\n")
        f.write("=" * 60 + "\n\n")
        f.write(tree_text)
    return tree_output_path


def collect_skill_paths(skill_dir: str) -> list:
    """扫出目录下全部skill的相对路径。"""
    rel_paths = []
    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for name in files:
            if name.endswith('.md'):
                rel_paths.append(
                    os.path.relpath(os.path.join(root, name), skill_dir).replace(os.sep, "/"))
    return sorted(rel_paths)


def paths_from_conversion_report(skill_dir: str) -> set:
    """从 conversion_report.json 取出蒸馏那一轮产出的skill路径，用于对比后续新增。
    报告不存在或读不动就返回空集（只影响提示，不影响生成）。"""
    report_path = os.path.join(skill_dir, "conversion_report.json")
    if not os.path.isfile(report_path):
        return set()
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()
    return {r.get("skill_path") for r in report.get("results", []) if r.get("skill_path")}


def main(skill_dir: str, dry_run: bool):
    print("=" * 60)
    print("重新生成skill树结构" + ("（预演，不写文件）" if dry_run else ""))
    print("=" * 60)
    print(f"skill目录: {skill_dir}\n")

    if not os.path.isdir(skill_dir):
        print(f"错误: 目录不存在: {skill_dir}")
        sys.exit(1)

    rel_paths = collect_skill_paths(skill_dir)
    if not rel_paths:
        print("错误: 目录下没有找到.md文件")
        sys.exit(1)

    tree_text = render_tree(rel_paths, skill_dir)

    # 蒸馏那轮之后新增/删除的skill：这正是旧树结构对不上的原因，值得单独点出来
    distilled = paths_from_conversion_report(skill_dir)
    if distilled:
        added = sorted(set(rel_paths) - distilled)
        missing = sorted(distilled - set(rel_paths))
        if added:
            print(f"蒸馏之后新增的 {len(added)} 篇skill（旧树结构里没有）:")
            for rel in added:
                print(f"  + {rel}")
        if missing:
            print(f"\nconversion_report.json 里有记录但目录下找不到的 {len(missing)} 篇"
                  f"（转换失败或已删除）:")
            for rel in missing:
                print(f"  - {rel}")
        if added or missing:
            print()

    deep = [p for p in rel_paths if p.count("/") > 1]
    if deep:
        print(f"[WARN] {len(deep)} 篇skill嵌套超过两级，叶子上会带出子路径:")
        for rel in deep:
            print(f"  - {rel}")
        print()

    print(tree_text)
    print()

    if dry_run:
        print(f"共 {len(rel_paths)} 篇skill；这是预演，未写文件"
              f"（去掉 --dry-run 才写入 {TREE_FILENAME}）。")
        return

    path = write_tree_file(skill_dir, tree_text)
    print(f"共 {len(rel_paths)} 篇skill，树结构已写入: {path}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    main(
        args[0] if args else f"skills_distilled/{datetime.now().strftime('%m-%d')}",
        "--dry-run" in sys.argv[1:],
    )
