#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同一批评测跑多个模型，比较哪个更适合做skill优化。

评测优化是三条流水线里最吃推理的一环：要从评测结论反推出skill里哪一步判据误导了
排障agent，再整篇重写近万字符的skill而不丢原有场景。哪个模型胜任不该靠猜，本脚本
把同一批评测分别喂给多个模型，各自出一份报告，再按客观信号并排比较：

  - 是否被落盘前的校验判失败（篇幅缩水 / 省略写法 / 围栏截断 / 自引用）
  - 篇幅与小节数的保留率（整篇重写最容易悄悄丢场景）
  - 判定的action、给出的fixes条数、耗时

每个模型的报告分别写到 reports/skill_optimize_report_<mm-dd>_<模型名>.md，可以直接
对着读改动内容。全程DRY-RUN，不会写任何skill文件。

用法：
  python3 compare_models.py                                   # 比较默认的两个模型
  python3 compare_models.py qwen3.6-27b MiniMax-M2.7-thinking  # 指定要比的模型
"""
import os
import re
import sys
import unicodedata
from datetime import datetime

from model_config import MODEL_PROFILES, resolve_model
from skill_eval_optimize_pipeline import main as optimize_main

DEFAULT_MODELS = ["qwen3.6-27b", "MiniMax-M2.7-thinking"]

EVALS_PATH = "evals"
SKILL_DIR = "skills_distilled/07-27"
WORKERS = 3


def _display_width(text: str) -> int:
    """中文占两格，按显示宽度算，否则含中文的列对不齐。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    """按显示宽度左对齐补空格；超宽则从左侧截断（路径尾部信息量更大）。"""
    while _display_width(text) > width - 1 and len(text) > 1:
        text = text[1:]
    return text + " " * max(0, width - _display_width(text))


def summarize(result: dict) -> dict:
    """把一篇skill的优化结果压成可并排比较的几个数。"""
    content = (result.get("content") or "").strip()
    original_chars = result.get("original_chars") or 0
    original_sections = result.get("original_sections") or 0
    sections = len(re.findall(r"^## +\S", content, re.MULTILINE))
    verdict = ("失败" if result.get("error") or result.get("invalid")
               else result.get("action", "?"))
    return {
        "target": result["target"],
        "verdict": verdict,
        "note": (result.get("error") or result.get("invalid")
                 or result.get("change_summary") or result.get("reason", ""))[:60],
        "chars": f"{original_chars}→{len(content)}" if content else "—",
        "char_ratio": (f"{len(content) / original_chars:.0%}"
                       if content and original_chars else "—"),
        "sections": (f"{original_sections}→{sections}" if content else "—"),
        "fixes": len(result.get("fixes") or ()),
        "elapsed": result.get("elapsed", "—"),
    }


def main(models: list):
    stamp = datetime.now().strftime("%m-%d")
    print("=" * 72)
    print(f"模型对比：{'、'.join(models)}")
    print("=" * 72)

    for name in models:
        if name not in MODEL_PROFILES:
            print(f"[WARN] {name} 未登记在 model_config.MODEL_PROFILES 里，"
                  f"将按默认档调用")
        try:
            cfg = resolve_model(name)
            print(f"  {name}: {cfg['api_url']}（thinking "
                  f"{'开' if cfg['thinking'] else '关'}，max_tokens {cfg['max_tokens']}）")
        except ValueError as e:
            print(f"错误: {e}")
            sys.exit(1)

    runs = []
    for name in models:
        print("\n" + "=" * 72)
        print(f"跑模型: {name}")
        print("=" * 72)
        report_path = f"reports/skill_optimize_report_{stamp}_{name}.md"
        try:
            run = optimize_main(
                EVALS_PATH=EVALS_PATH,
                SKILL_DIR=SKILL_DIR,
                API_URL=None,
                MODEL_NAME=name,
                WORKERS=WORKERS,
                REPORT_PATH=report_path,
                # 对比只看模型产出，绝不落盘
                DRY_RUN=True,
                # 失败也要继续跑下一个模型，否则比不出结果
                EXIT_ON_FAILURE=False,
            )
        except SystemExit as e:
            print(f"[FAIL] {name} 提前退出（{e}），跳过")
            continue
        except Exception as e:
            print(f"[FAIL] {name} 运行异常: {e}")
            continue
        runs.append(run)

    if not runs:
        print("\n错误: 没有任何模型跑出结果")
        sys.exit(1)

    print("\n" + "=" * 72)
    print("对比结果")
    print("=" * 72)
    cols = [("模型", 24), ("skill", 26), ("判定", 12), ("篇幅", 15),
            ("保留", 7), ("小节", 8), ("fixes", 7), ("秒", 8)]
    header = "".join(_pad(name, width) for name, width in cols)
    print(header)
    print("-" * _display_width(header))
    for run in runs:
        for result in run["results"]:
            s = summarize(result)
            row = [run["model"], s["target"], s["verdict"], s["chars"],
                   s["char_ratio"], s["sections"], str(s["fixes"]), str(s["elapsed"])]
            print("".join(_pad(value, width)
                          for value, (_, width) in zip(row, cols)))

    print("\n各模型的说明与报告:")
    for run in runs:
        failed = [r for r in run["results"] if r.get("error") or r.get("invalid")]
        print(f"\n● {run['model']} → {run['report_path']}")
        print(f"    {len(run['results'])} 篇，失败 {len(failed)} 篇"
              f"，未匹配评测 {len(run['unresolved'])} 条")
        for result in run["results"]:
            s = summarize(result)
            print(f"    - {s['target']}: {s['verdict']}｜{s['note']}")

    print("\n" + "=" * 72)
    print("怎么判优劣（数字之外必须人工看的部分）:")
    print("  1. 报告里的 fixes 有没有点到评测真正暴露的那处判据——点不到就是没看懂，")
    print("     篇幅、小节数再漂亮也不算过。")
    print("  2. 保留率明显低于100%时，逐一确认少掉的是合并重复小节还是丢了场景。")
    print("  3. 两份报告的“改动内容”并排读，看谁改的是判据本身、谁只是换了措辞。")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    main(args or DEFAULT_MODELS)
