#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从蒸馏出的skill中抽取display查询命令，按源文件路径一一对照输出为json。

skill正文里的命令要么写成行内代码（`` `display xxx` ``），要么写在围栏代码块
（```...```）里逐行列出，两种都抽取，按出现顺序去重后输出为list of string。

用法：
  python3 extract_display_commands.py                          # 抽取今天的 skills_distilled/mm-dd
  python3 extract_display_commands.py <skill目录>               # 输出到 cmd_distilled/<skill目录最后一级>
  python3 extract_display_commands.py <skill目录> <输出目录>
"""
import json
import os
import re
import sys
from datetime import datetime

# 行内代码 `...` 与围栏代码块 ```...``` 中以display开头的一行/一段
INLINE_CODE_PATTERN = re.compile(r"`([^`\n]+)`")
FENCE_BLOCK_PATTERN = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
DISPLAY_COMMAND_PATTERN = re.compile(r"^display\b.*")


def extract_display_commands(content: str) -> list:
    """从skill正文中按出现顺序抽取去重后的display命令列表。"""
    commands = []
    seen = set()

    def add(candidate: str):
        cmd = candidate.strip()
        match = DISPLAY_COMMAND_PATTERN.match(cmd)
        if not match:
            return
        cmd = match.group(0).strip()
        if cmd not in seen:
            seen.add(cmd)
            commands.append(cmd)

    for span in INLINE_CODE_PATTERN.findall(content):
        add(span)

    for block in FENCE_BLOCK_PATTERN.findall(content):
        for line in block.splitlines():
            add(line)

    return commands


def get_all_markdown_files(skill_dir: str) -> list:
    md_files = []
    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for file in files:
            if file.endswith('.md'):
                md_files.append(os.path.join(root, file))
    return sorted(md_files)


def main(SOURCE_DIR: str, OUTPUT_DIR: str):
    print("=" * 60)
    print("Display命令抽取")
    print("=" * 60)
    print(f"skill目录: {SOURCE_DIR}")
    print(f"输出目录: {OUTPUT_DIR}\n")

    if not os.path.isdir(SOURCE_DIR):
        print(f"错误: 目录不存在: {SOURCE_DIR}")
        sys.exit(1)

    md_files = get_all_markdown_files(SOURCE_DIR)
    if not md_files:
        print("错误: 目录下没有找到.md文件")
        sys.exit(1)

    total_commands = 0
    empty_files = []

    for md_path in md_files:
        rel_path = os.path.relpath(md_path, SOURCE_DIR)
        json_rel_path = rel_path[:-3] + ".json"
        json_path = os.path.join(OUTPUT_DIR, json_rel_path)

        with open(md_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        commands = extract_display_commands(content)

        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(commands, f, ensure_ascii=False, indent=2)

        total_commands += len(commands)
        if not commands:
            empty_files.append(rel_path)
        print(f"  {rel_path} -> {json_path} ({len(commands)} 条)")

    print("\n" + "=" * 60)
    print(f"共处理 {len(md_files)} 个skill，抽取 {total_commands} 条display命令")
    if empty_files:
        print(f"其中 {len(empty_files)} 个skill未抽到命令:")
        for rel_path in empty_files:
            print(f"  - {rel_path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    source_dir = args[0] if args else f"skills_distilled/{datetime.now().strftime('%m-%d')}"
    output_dir = args[1] if len(args) > 1 else os.path.join(
        "cmd_distilled", os.path.basename(os.path.normpath(source_dir)))
    main(source_dir, output_dir)
