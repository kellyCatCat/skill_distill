#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端跑一遍网页前端，不需要内网。

和 `mock_run_excel_pipeline.py` 同一个路子：只把 `requests.post` 换掉，其余全是
真的在走——真的起 HTTP 服务、真的用浏览器那种 multipart 请求传 xlsx、真的解析、
真的过校验器、真的写文件。所以这份测试能守住的是网页这层自己的东西：

  A. 上传 → 解析 + 体检，且**一次模型调用都不发**（两步走的前一步）；
  B. 生成 → 轮询 → 每篇拿到 skill 全文；
  C. 下载：单篇 .md、zip（含改写说明）、改写说明本身；
  D. 落盘：只写校验通过的，重复落盘不重复写；
  E. 违规回复要落在"失败"上，且**一个文件都不许写出去**；
  F. 坏输入（非 xlsx、越界的场景号、不存在的任务）要被挡在 4xx。

用法：
  python3 test_web_server.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

import skill_self_distill_pipeline as distill
import web_server
from mock_run_excel_pipeline import BAD_REPLY, GOOD_REPLY, MOCK_URL, FakeResponse

XLSX_PATH = "excel_cases/排障步骤表.xlsx"
BOUNDARY = "----WebKitFormBoundaryTest12345"

# 当前这一轮要让假模型回什么。违规那一例要单独换，所以做成可改的
REPLY = {"text": GOOD_REPLY}


def install_mock():
    def fake_post(url, json=None, headers=None, timeout=None, verify=None):
        return FakeResponse(REPLY["text"], sse=False)
    distill.requests.post = fake_post


def multipart(fields: list, files: list) -> tuple:
    """按浏览器的写法拼一个 multipart/form-data 请求体。

    刻意不用任何辅助库：服务端那个解析器就是要吃这种东西，自己拼才验得到它
    对二进制正文（xlsx 里什么字节都有）处理得对不对。
    """
    body = io.BytesIO()
    for name, value in fields:
        body.write(f"--{BOUNDARY}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.write(value.encode("utf-8") + b"\r\n")
    for name, filename, data in files:
        body.write(f"--{BOUNDARY}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{name}"; '
                   f'filename="{filename}"\r\n'.encode())
        body.write(b"Content-Type: application/octet-stream\r\n\r\n")
        body.write(data + b"\r\n")
    body.write(f"--{BOUNDARY}--\r\n".encode())
    return body.getvalue(), f"multipart/form-data; boundary={BOUNDARY}"


class Client:
    def __init__(self, base: str):
        self.base = base

    def request(self, path: str, data=None, content_type=None) -> tuple:
        req = urllib.request.Request(self.base + path, data=data)
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def json(self, path: str, payload=None) -> tuple:
        if payload is None:
            status, body, _ = self.request(path)
        else:
            status, body, _ = self.request(
                path, json.dumps(payload).encode("utf-8"), "application/json")
        return status, json.loads(body.decode("utf-8"))

    def upload(self, files: list, category: str = "排障步骤") -> tuple:
        body, ctype = multipart([("category", category)], files)
        status, raw, _ = self.request("/api/upload", body, ctype)
        return status, json.loads(raw.decode("utf-8"))


def wait_done(client: Client, job_id: str, timeout: int = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, job = client.json(f"/api/job?job={job_id}")
        if job["status"] != "running":
            return job
        time.sleep(0.2)
    raise AssertionError("生成一直没结束")


CHECKS = []


def check(label: str, condition, detail: str = "") -> None:
    CHECKS.append((label, bool(condition), detail))
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition and detail:
        print(f"        {detail}")


def run() -> int:
    install_mock()
    workdir = tempfile.mkdtemp(prefix="web-test-")
    jobs_dir = os.path.join(workdir, "jobs")
    output_dir = os.path.join(workdir, "skills")
    os.makedirs(jobs_dir)

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    server.workers, server.default_output = 2, output_dir
    server.max_tokens, server.timeout_seconds, server.jobs_dir = None, 600, jobs_dir
    # 假端点：requests.post 已经被换掉，这个地址只是让 resolve_model 不去读 .env
    server.api_url = MOCK_URL
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = Client(f"http://127.0.0.1:{server.server_address[1]}")

    try:
        xlsx = open(XLSX_PATH, "rb").read()

        # ---- 页面本身发得出去 ----
        status, body, _ = client.request("/")
        check("GET / 返回页面", status == 200 and b"<title>" in body, f"status={status}")
        _, models = client.json("/api/models")
        check("模型清单可用", models["default"] in models["models"], str(models))

        # ---- A. 上传只做解析和体检，不调模型 ----
        calls = []
        original = distill.requests.post
        distill.requests.post = lambda *a, **kw: calls.append(1) or original(*a, **kw)
        status, job = client.upload([("file", "排障步骤表.xlsx", xlsx)])
        check("上传后解析出场景", status == 200 and job["scenarios"], json.dumps(job)[:200])
        check("上传阶段一次模型都没调", not calls, f"调了{len(calls)}次")
        check("场景带着输出路径与步骤数",
              all(s["skill_path"].endswith(".md") and s["steps"] > 0
                  for s in job["scenarios"]),
              str(job["scenarios"]))
        check("体检结果随上传一起返回", isinstance(job["audit"], list))

        # ---- B. 生成 ----
        status, started = client.json("/api/generate",
                                      {"job": job["job"], "indexes": [0],
                                       "model": models["default"]})
        check("生成请求被接受", status == 200 and started["status"] in ("running", "done"),
              str(started)[:200])
        done = wait_done(client, job["job"])
        first = done["scenarios"][0]
        check("生成完成且这一篇通过校验", first["state"] == "ok", first.get("failure", ""))
        check("拿到了skill全文", first["content"].startswith("---"),
              first["content"][:80])

        # ---- C. 下载 ----
        status, md, headers = client.request(f"/api/skill?job={job['job']}&index=0")
        check("单篇下载是markdown",
              status == 200 and md.decode("utf-8").startswith("---")
              and "attachment" in headers.get("Content-Disposition", ""),
              f"status={status}")
        status, raw, _ = client.request(f"/api/zip?job={job['job']}&indexes=0")
        names = zipfile.ZipFile(io.BytesIO(raw)).namelist()
        check("zip 里有skill和改写说明",
              status == 200 and first["skill_path"] in names and "改写说明.md" in names,
              str(names))
        status, report, _ = client.request(f"/api/report?job={job['job']}")
        check("改写说明可下载且格式与命令行一致",
              status == 200 and "## 新建skill：" in report.decode("utf-8"),
              report[:120].decode("utf-8", "replace"))

        # ---- D. 落盘（落盘前后各看一次现有 skill 库）----
        _, before = client.json(f"/api/skills?dir={urllib.parse.quote(output_dir)}")
        check("落盘前库是空的", before["exists"] is False and before["total"] == 0,
              str(before))
        status, applied = client.json("/api/apply",
                                      {"job": job["job"], "indexes": [0],
                                       "output_dir": output_dir})
        landed = os.path.join(output_dir, *first["skill_path"].split("/"))
        check("落盘写出了文件",
              status == 200 and os.path.isfile(landed)
              and applied["applied"][0]["state"] == "created",
              str(applied.get("applied")))
        _, again = client.json("/api/apply", {"job": job["job"], "indexes": [0],
                                              "output_dir": output_dir})
        check("重复落盘不重复写", again["applied"][0]["state"] == "skipped",
              str(again["applied"]))

        _, library = client.json(f"/api/skills?dir={urllib.parse.quote(output_dir)}")
        listed = [s for g in library["groups"] for s in g["skills"]]
        check("落盘后能在skill库里看到它",
              library["total"] == 1 and listed[0]["path"] == first["skill_path"]
              and listed[0]["description"],
              str(library))
        check("库里按一级目录归拢",
              library["groups"][0]["level1"] == first["skill_path"].split("/")[0],
              str(library["groups"]))

        # ---- E. 违规回复：判失败，且不落盘 ----
        REPLY["text"] = BAD_REPLY
        status, bad_job = client.upload([("file", "排障步骤表.xlsx", xlsx)])
        client.json("/api/generate", {"job": bad_job["job"], "indexes": [0]})
        bad_done = wait_done(client, bad_job["job"])
        bad_output = os.path.join(workdir, "skills-bad")
        _, bad_applied = client.json("/api/apply",
                                     {"job": bad_job["job"], "indexes": [0],
                                      "output_dir": bad_output})
        check("违规回复被判失败", bad_done["scenarios"][0]["state"] == "failed",
              str(bad_done["scenarios"][0])[:200])
        check("违规内容一个文件都没写出去",
              bad_applied["applied"][0]["state"] == "skipped"
              and not os.path.isdir(bad_output),
              str(bad_applied["applied"]))
        REPLY["text"] = GOOD_REPLY

        # ---- F. 坏输入 ----
        status, error = client.upload([("file", "说明.txt", b"not a workbook")])
        check("非xlsx被拒", status == 400 and "xlsx" in error.get("error", ""), str(error))
        status, error = client.json("/api/generate", {"job": job["job"], "indexes": [99]})
        check("越界的场景号被拒", status == 400 and "越界" in error.get("error", ""), str(error))
        status, error = client.json("/api/job?job=" + "0" * 32)
        check("不存在的任务返回404", status == 404, str(error))
        # 任务号是外部输入，形状不对的要在查表之前就被挡住。中文得先URL编码，
        # 否则 urllib 在客户端这边就把请求行编码坏了，压根发不到服务端
        status, error = client.json(
            "/api/job?job=" + urllib.parse.quote("不是任务号"))
        check("任务号格式不对也返回404", status == 404, str(error))

        # ---- multipart 解析器：二进制正文要一字节不差 ----
        body, ctype = multipart([("category", "排障步骤")],
                                [("file", "表.xlsx", xlsx)])
        parts = web_server.parse_multipart(body, ctype)
        check("multipart 解析出字段与文件", len(parts) == 2)
        check("xlsx 字节原样还原",
              parts[1]["data"] == xlsx and parts[1]["filename"] == "表.xlsx",
              f"{len(parts[1]['data'])} vs {len(xlsx)}")
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(workdir, ignore_errors=True)

    failures = sum(1 for _, ok, _ in CHECKS if not ok)
    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} 通过")
    return failures


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
