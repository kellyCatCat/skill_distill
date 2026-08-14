#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用假的HTTP响应跑通 excel_skill_distill_pipeline，不需要内网。

蒸馏用的模型接口只在内网可达，所以在别的机器上改这条流水线时，没有任何办法确认
改动有没有把链路弄坏。这里只替换 requests.post 一层，`call_model_with_retry`
本身照常执行——retry、SSE解析、extractor校验、多进程、落盘、报告全是真的在走，
只有模型的回复是事先写好的。

覆盖三种情形：
  A. 合规回复 + 普通JSON响应
  B. 合规回复 + SSE流响应（MiniMax那个部署无视 stream=False，实际就返回SSE；
     跑这条是为了确认流被拼回来之后与普通JSON路径逐字一致、中文不乱码）
  C. 违规回复（命令没用反引号、参数是花括号）——必须被 extractor 拦住并重试到失败，
     且一个文件都不许写出去

用法：
  python3 mock_run_excel_pipeline.py           # 三种都跑
  python3 mock_run_excel_pipeline.py json|sse|bad
"""
import json
import os
import re
import shutil
import sys
import tempfile

import skill_self_distill_pipeline as distill
import excel_skill_distill_pipeline as pipeline
from test_excel_skill_format import GOOD

# 不会真的被访问到：requests.post 已被替换，这里只是让 resolve_model 不去读 .env
MOCK_URL = "http://mock.invalid/v1/chat/completions"

XLSX_PATH = "excel_cases/排障步骤表.xlsx"

GOOD_REPLY = """```json
{"scenario": "SRv6 TE Policy down告警", "steps": 8,
 "branches_expanded": "步骤1的四分支长句、步骤5/6/7/8的跳过条件已拆成逐条",
 "commands_normalized": [{"from": "<endpointipv6>", "to": "<endpoint-ipv6>"},
                         {"from": "<colorid>", "to": "<color-id>"}]}
```
```markdown
""" + GOOD + """
```"""

BAD_REPLY = GOOD_REPLY.replace(
    "`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`",
    "display srv6-te policy endpoint {endpointipv6} color {colorid}")


class FakeResponse:
    """够 call_model_with_retry 用的最小响应对象。"""

    def __init__(self, reply: str, sse: bool):
        self.status_code = 200
        self.encoding = "utf-8"
        if sse:
            self.headers = {"Content-Type": "text/event-stream"}
            # 切成多个 data: 块，模拟真实流式——正文是被拼起来的，不是一次给全
            lines = ["data: " + json.dumps(
                {"choices": [{"delta": {"content": reply[i:i + 200]}}]},
                ensure_ascii=False) for i in range(0, len(reply), 200)]
            lines.append("data: " + json.dumps(
                {"choices": [{"delta": {}, "finish_reason": "stop"}]}))
            lines.append("data: [DONE]")
            self.text = "\n".join(lines)
            self._json = None
        else:
            self.headers = {"Content-Type": "application/json"}
            self._json = {"choices": [{"message": {"content": reply},
                                       "finish_reason": "stop"}]}
            self.text = json.dumps(self._json, ensure_ascii=False)

    def raise_for_status(self):
        pass

    def json(self):
        if self._json is None:
            raise ValueError("SSE响应不是JSON")
        return self._json


def install_mock(reply: str, sse: bool):
    def fake_post(url, json=None, headers=None, timeout=None, verify=None):
        return FakeResponse(reply, sse)
    distill.requests.post = fake_post


def run(label: str, reply: str, sse: bool, workdir: str,
        model: str = "qwen3.6-27b") -> tuple:
    """跑一轮，返回 (退出码, 报告路径)。

    model 决定 payload 走哪条分支：不开思考的模型会多发一个关思考的
    chat_template_kwargs，开思考的不发。两条都要覆盖，否则换默认模型时
    另一条分支就没人测了。
    """
    install_mock(reply, sse)
    report_path = os.path.join(workdir, "report.md")
    print("\n" + "#" * 72)
    print(f"# {label}（{'SSE流' if sse else '普通JSON'}，{model}）")
    print("#" * 72)
    code = 0
    try:
        pipeline.main(
            XLSX_PATH=XLSX_PATH,
            OUTPUT_DIR=os.path.join(workdir, "skills"),
            API_URL=MOCK_URL,
            MODEL_NAME=model,
            WORKERS=2,
            REPORT_PATH=report_path,
            DRY_RUN=True,
        )
    except SystemExit as e:
        code = e.code or 0
    print(f"\n>>> {label}: 退出码={code}")
    return code, report_path


def skill_body(report_path: str) -> str:
    """从报告里取出改动内容块，用来比对两条响应路径的产出。"""
    text = open(report_path, encoding="utf-8").read()
    match = re.search(r"````markdown\n(.*?)\n````", text, re.DOTALL)
    return match.group(1) if match else ""


def main(which: str):
    workdir = tempfile.mkdtemp(prefix="mock-excel-")
    failures = []
    try:
        bodies = {}
        if which in ("all", "json"):
            code, report = run("A. 合规回复", GOOD_REPLY, False,
                               os.path.join(workdir, "a"))
            bodies["json"] = skill_body(report)
            if code != 0:
                failures.append("A 合规回复应当成功，实际退出码非0")
        if which in ("all", "sse"):
            # SSE 是 MiniMax 那个部署的形态，所以这条特意用它——顺带覆盖
            # thinking=True 时不发 chat_template_kwargs 的那条分支
            code, report = run("B. 合规回复 / SSE流", GOOD_REPLY, True,
                               os.path.join(workdir, "b"), model="MiniMax-M2.7")
            bodies["sse"] = skill_body(report)
            if code != 0:
                failures.append("B SSE流应当成功，实际退出码非0")
        if which in ("all", "bad"):
            code, report = run("C. 违规回复", BAD_REPLY, False,
                               os.path.join(workdir, "c"))
            if code == 0:
                failures.append("C 违规回复本应被拦下，却判成了成功")
            if skill_body(report):
                failures.append("C 违规回复不该产出可落盘的内容")

        if "json" in bodies and "sse" in bodies:
            if bodies["json"] != bodies["sse"]:
                failures.append("SSE拼回的正文与普通JSON路径不一致")
            elif "根因对照表" not in bodies["sse"]:
                failures.append("SSE路径的中文疑似乱码")

        print("\n" + "=" * 72)
        if failures:
            for item in failures:
                print(f"[FAIL] {item}")
        else:
            print("[OK] 三种情形均符合预期："
                  "合规回复能产出skill，SSE与JSON逐字一致，违规回复被拦下且未落盘")
        return 1 if failures else 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "all"))
