#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排障步骤表改写为skill的网页前端：传表格 → 看解析 → 生成 → 审阅 → 落盘。

和 `excel_skill_distill_pipeline.py` 命令行入口的关系是"同一条流水线的另一个外壳"：
解析、体检、prompt、校验、报告全部复用那边的函数，这里只负责收文件、开线程、把
中间结果端到页面上。所以校验规则改了之后，网页这边不用跟着改。

流程按两步走，和 `--check` 的用意一致：
  1. POST /api/upload  存表格 → 解析场景 → 体检（ragIndex重号/步骤编号不连续/路径冲突），
     **不调模型**。表有问题时先在页面上看到，省下一整轮模型调用；
  2. POST /api/generate 才真的调模型，每个场景一次，线程池并发；
  3. 生成的内容默认**不落盘**（和流水线的 DRY_RUN 一致），页面上逐篇审，
     确认后 POST /api/apply 才写进 skill 目录，或者直接下载 zip / 改写说明。

只用标准库：内网机器上 `python3 web_server.py` 就能跑，不必再装 web 框架。
模型调用仍然要能连上 .env 里配的地址。

用法：
  python3 web_server.py                      # 127.0.0.1:8000
  python3 web_server.py --port 8080 --host 0.0.0.0
  python3 web_server.py --workers 4
"""
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import traceback
import uuid
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from multiprocessing.pool import ThreadPool
from urllib.parse import parse_qs, urlparse

from excel_skill_distill_pipeline import (DEFAULT_CATEGORY, DEFAULT_MODEL,
                                          audit_commands, audit_skill_paths,
                                          audit_step_numbers, build_report,
                                          check_skill_format, convert_scenario,
                                          derive_skill_path, load_scenarios)
from model_config import MODEL_PROFILES
from skill_case_merge_pipeline import build_skill_index
from validate_skills import check_skill_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE_DIR, "web", "index.html")

# 上传上限。一张排障步骤表几十KB到几MB，32MB 足够；设上限是为了不让一个超大
# 文件把内存吃光——请求体是一次读进来的。
MAX_UPLOAD = 32 * 1024 * 1024
ALLOWED_SUFFIXES = (".xlsx", ".xlsm")

# 任务只活在内存里（外加一个临时目录放上传的表格与生成的报告）。进程重启就没了，
# 这是刻意的：真正要留下来的是落盘的 skill 和下载走的报告，中间态不值得持久化。
JOBS = {}
JOBS_LOCK = threading.Lock()
JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


# ------------------------------------------------------------------ 任务

def new_job(workdir: str) -> dict:
    job = {
        "id": uuid.uuid4().hex,
        "dir": workdir,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": [],
        "scenarios": [],
        "audit": [],
        "category": DEFAULT_CATEGORY,
        "status": "parsed",     # parsed / running / done
        "model": None,
        "results": {},          # 场景序号 → convert_scenario 的结果
        "progress": {"done": 0, "total": 0},
        "report": None,
        "error": None,
    }
    with JOBS_LOCK:
        JOBS[job["id"]] = job
    return job


def get_job(job_id: str) -> dict:
    if not job_id or not JOB_ID_PATTERN.match(job_id):
        raise KeyError("任务号格式不对")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise KeyError("任务不存在或已随服务重启失效，请重新上传表格")
    return job


def scenario_view(job: dict, index: int) -> dict:
    """一个场景端给页面的样子：解析结果 + 这一篇的生成状态。"""
    scenario = job["scenarios"][index]
    result = job["results"].get(index)
    view = {
        "index": index,
        "name": scenario["name"],
        "sheet": scenario["sheet"],
        "source": os.path.basename(scenario.get("source") or ""),
        "rows": list(scenario["rows"]),
        "steps": len(scenario["steps"]),
        "skill_path": derive_skill_path(scenario, job["category"]),
        "state": "pending",
    }
    if result:
        failure = result.get("error") or result.get("invalid")
        view["state"] = "failed" if failure else "ok"
        view["failure"] = failure or ""
        view["content"] = result.get("content", "")
        view["branches_expanded"] = result.get("branches_expanded", "")
        view["commands_normalized"] = result.get("commands_normalized", [])
        view["repaired"] = result.get("repaired", "")
        view["edited"] = bool(result.get("edited"))
        # 三次重试全废时也会留着最后一次的内容：多数只差一两处，人工改比重跑快
        view["needs_manual_fix"] = bool(result.get("needs_manual_fix"))
    return view


def job_view(job: dict) -> dict:
    return {
        "job": job["id"],
        "created": job["created"],
        "files": job["files"],
        "category": job["category"],
        "status": job["status"],
        "model": job["model"],
        "progress": job["progress"],
        "error": job["error"],
        "audit": [{"name": name, "issues": issues} for name, issues in job["audit"]],
        "scenarios": [scenario_view(job, i) for i in range(len(job["scenarios"]))],
        "has_report": bool(job["report"]),
    }


def skill_library(directory: str) -> dict:
    """落盘目录里已经有哪些 skill，按一级目录归拢。

    落盘之前想知道的是"这一篇是新增还是覆盖"——光看路径记不住目录里已经有什么，
    所以把现有的列出来，页面上再把撞名的标出来。描述取自 frontmatter，与案例合并
    流水线挑目标 skill 时读的是同一份索引。
    """
    view = {"dir": os.path.abspath(directory), "exists": os.path.isdir(directory),
            "total": 0, "groups": []}
    if not view["exists"]:
        return view
    groups = {}
    for item in build_skill_index(directory):
        parts = item["rel_path"].split("/")
        level1 = parts[0] if len(parts) > 1 else "（直接放在根目录）"
        groups.setdefault(level1, []).append({
            "path": item["rel_path"],
            "name": parts[-1],
            "description": item["description"],
            "sections": len(item["headings"]),
            "chars": len(item["content"]),
        })
    view["groups"] = [{"level1": name, "skills": sorted(items, key=lambda s: s["path"])}
                      for name, items in sorted(groups.items())]
    view["total"] = sum(len(g["skills"]) for g in view["groups"])
    return view


def parse_upload(job: dict, category: str) -> None:
    """解析已经存进任务目录的表格，并做一次体检（不调模型）。"""
    job["category"] = category
    job["scenarios"] = load_scenarios(job["dir"])
    audit = []
    for scenario in job["scenarios"]:
        issues = audit_commands(scenario) + audit_step_numbers(scenario)
        if issues:
            audit.append((scenario["name"], issues))
    path_issues = audit_skill_paths(job["scenarios"], category)
    if path_issues:
        audit.append(("输出路径冲突", path_issues))
    job["audit"] = audit


def run_generation(job: dict, indexes: list, model: str, workers: int,
                   max_tokens, timeout: int, api_url: str = None) -> None:
    """在后台线程里跑改写。用线程池而不是进程池：模型调用是等网络，
    线程足够，也省得在服务进程里 fork。"""
    tasks = [(i, job["scenarios"][i],
              derive_skill_path(job["scenarios"][i], job["category"]))
             for i in indexes]
    job["progress"] = {"done": 0, "total": len(tasks)}
    job["status"] = "running"
    job["model"] = model
    job["error"] = None

    def convert(task):
        index, scenario, skill_path = task
        try:
            return index, convert_scenario(
                (scenario, skill_path, api_url, model, max_tokens, timeout))
        except Exception as e:
            # 一篇炸了不该带走整批（模型地址没配、表里某个格子的类型意外……）：
            # 把它记成这一篇的失败，其余照跑，页面上也就能看出是哪一篇的事
            traceback.print_exc()
            return index, {"scenario": scenario["name"], "skill_path": skill_path,
                           "steps": len(scenario["steps"]), "rows": scenario["rows"],
                           "error": f"错误：{type(e).__name__}: {e}"}

    try:
        with ThreadPool(processes=max(1, min(workers, len(tasks)))) as pool:
            for index, result in pool.imap_unordered(convert, tasks):
                with JOBS_LOCK:
                    job["results"][index] = result
                    job["progress"]["done"] += 1
    except Exception as e:                      # 线程里抛出来就没人接了，记进任务
        job["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        job["status"] = "done"
        write_report(job)


def write_report(job: dict) -> None:
    """写一份和命令行流水线格式一致的改写说明。

    格式一致才能继续用 `apply_change_report.py` 落盘——页面上的"落盘"按钮只是
    另一条路，报告这条路仍然要走得通（有人习惯先把报告存档再落盘）。
    """
    ordered = [job["results"][i] for i in sorted(job["results"])]
    if not ordered:
        return
    report = build_report(ordered, job["audit"], "、".join(job["files"]),
                          job.get("output_dir") or "skills_from_excel/<mm-dd>",
                          dry_run=True)
    path = os.path.join(job["dir"], "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    job["report"] = path


def edit_result(job: dict, index: int, content: str) -> dict:
    """把页面上改过的正文存回这一篇，并重新跑一遍校验。

    模型的产出常常只差一两处（少个反引号、多了一条编造的命令），改这一两处比重跑
    一整轮划算得多。改完仍然过一遍校验器：不是为了拦住人，而是让人知道自己改完之后
    这篇算不算合规——落盘时按这个结果区别对待。
    """
    scenario = job["scenarios"][index]
    content = (content or "").strip()
    if not content:
        raise ValueError("改后的内容是空的")
    result = job["results"].get(index) or {
        "scenario": scenario["name"],
        "skill_path": derive_skill_path(scenario, job["category"]),
        "steps": len(scenario["steps"]), "rows": scenario["rows"]}
    result["content"] = content
    result["edited"] = True
    # 人工改过之后，之前那条失败原因就过期了：重新判一次，两个字段一起更新
    result.pop("error", None)
    result.pop("needs_manual_fix", None)
    result["invalid"] = check_skill_format(content, scenario)
    with JOBS_LOCK:
        job["results"][index] = result
    write_report(job)
    return result


def edit_landed_skill(directory: str, rel_path: str, content: str,
                      force: bool = False) -> dict:
    """改写已经落盘的 skill 文件。

    落盘之后才发现要改一句话，是常事。这里只允许改**已经存在**的文件：新建走生成
    那条路，否则页面就成了一个能往任意路径写文件的口子。

    校验用 `validate_skills.check_skill_file`（就是命令行校验整个 skill 库用的
    那个），它按文件校验，所以先写到同目录的临时文件上再判——有 ERROR 就不落，
    除非调用方明说要强落。
    """
    root = os.path.abspath(directory)
    path = os.path.abspath(os.path.join(root, *rel_path.split("/")))
    if os.path.commonpath([root, path]) != root or not path.endswith(".md"):
        raise ValueError(f"路径不合法: {rel_path}")
    if not os.path.isfile(path):
        raise ValueError(f"文件不存在，只能改已经落盘的 skill: {rel_path}")
    content = (content or "").strip()
    if not content:
        raise ValueError("改后的内容是空的")

    known = {os.path.relpath(os.path.join(root_dir, name), root).replace(os.sep, "/")
             for root_dir, _, names in os.walk(root)
             for name in names if name.endswith(".md")}
    # 先写到同目录的临时文件上再校验，通过了才顶替原文件：校验器按文件工作，
    # 而"校验没过就不该动原文件"必须是硬的——os.replace 是原子的，中途失败也
    # 不会留下半份内容。
    temp = path + ".editing"
    with open(temp, "w", encoding="utf-8") as f:
        f.write(content + "\n")
    try:
        issues = check_skill_file(temp, known_paths=known)
        errors = [desc for level, desc in issues if level == "ERROR"]
        warns = [desc for level, desc in issues if level == "WARN"]
        if errors and not force:
            return {"saved": False, "path": rel_path,
                    "errors": errors, "warns": warns}
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):      # 拒绝落盘或中途出错时，临时文件不留下
            os.remove(temp)
    return {"saved": True, "path": rel_path, "errors": errors, "warns": warns}


def apply_results(job: dict, indexes: list, output_dir: str,
                  force: bool = False) -> list:
    """把选中的、且校验通过的几篇写进 skill 目录，返回逐篇结果。"""
    root = os.path.abspath(output_dir)
    applied = []
    for index in indexes:
        result = job["results"].get(index)
        if not result or not result.get("content"):
            applied.append({"index": index, "state": "skipped",
                            "note": "还没有生成内容"})
            continue
        failure = result.get("error") or result.get("invalid")
        # 模型直出的违规内容一律不落盘；人工改过的可以，但要调用方明说——
        # 改的人自己就是判断依据，校验器的意见仍然记进报告，不悄悄放行
        if failure and not (force and result.get("edited")):
            applied.append({"index": index, "state": "skipped",
                            "note": ("没有通过校验，不落盘"
                                     if not result.get("edited") else
                                     "人工改过但仍未通过校验；要落盘请勾选"
                                     "「允许落盘未通过校验的人工修改」")})
            continue
        rel = result["skill_path"]
        path = os.path.abspath(os.path.join(root, *rel.split("/")))
        # 路径由 derive_skill_path 推出、文件名已过滤过分隔符，这里再确认一次
        # 结果确实落在输出目录内，不让任何输入把文件写到目录外面去
        if os.path.commonpath([root, path]) != root:
            applied.append({"index": index, "state": "failed",
                            "note": f"路径越出输出目录: {rel}"})
            continue
        content = result["content"].strip() + "\n"
        existed = os.path.isfile(path)
        if existed and open(path, encoding="utf-8").read() == content:
            applied.append({"index": index, "state": "skipped", "path": rel,
                            "note": "内容与现有文件一致，跳过"})
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        note = "覆盖了同名文件" if existed else "已新建"
        if failure:
            note += f"（人工修改，未通过校验：{failure}）"
        elif result.get("edited"):
            note += "（人工修改过）"
        applied.append({"index": index, "state": "overwritten" if existed else "created",
                        "path": rel, "note": note})
    job["output_dir"] = output_dir
    write_report(job)
    return applied


def make_zip(job: dict, indexes: list) -> bytes:
    """把选中的几篇打成 zip，按 skill 相对路径分目录。"""
    import io
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for index in indexes:
            result = job["results"].get(index)
            if not result or not result.get("content"):
                continue
            zf.writestr(result["skill_path"], result["content"].strip() + "\n")
        if job["report"] and os.path.isfile(job["report"]):
            zf.write(job["report"], "改写说明.md")
    return buffer.getvalue()


# ------------------------------------------------------------------ multipart

BOUNDARY_PATTERN = re.compile(r'boundary="?([^";]+)"?')
DISPOSITION_NAME = re.compile(r'name="([^"]*)"')
DISPOSITION_FILENAME = re.compile(r'filename="([^"]*)"')


def parse_multipart(body: bytes, content_type: str) -> list:
    """解析 multipart/form-data，返回 [{name, filename, data}]。

    标准库里没有现成的：`cgi` 已经从 3.13 移除，`email` 那条路要先把请求头拼回去、
    还得当心它把二进制正文按文本处理。xlsx 是二进制，所以这里按分隔线自己切，
    全程只在 bytes 上操作。
    """
    match = BOUNDARY_PATTERN.search(content_type or "")
    if not match:
        raise ValueError("Content-Type 里没有 boundary，不是合法的 multipart 请求")
    parts = []
    for chunk in body.split(b"--" + match.group(1).encode()):
        if chunk.startswith(b"--"):
            continue                       # 收尾的 --boundary--
        chunk = chunk.lstrip(b"\r\n")
        head, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue                       # 前导空白段
        if data.endswith(b"\r\n"):
            data = data[:-2]               # 分隔线之前的那个换行不属于内容
        headers = head.decode("utf-8", "replace")
        name = DISPOSITION_NAME.search(headers)
        filename = DISPOSITION_FILENAME.search(headers)
        parts.append({"name": name.group(1) if name else "",
                      "filename": filename.group(1) if filename else None,
                      "data": data})
    return parts


def safe_filename(name: str) -> str:
    """取文件名部分并去掉路径分隔符：上传的文件名是外部输入，不能拿它拼路径。"""
    name = (name or "").replace("\\", "/").split("/")[-1]
    return re.sub(r'[^\w.\-（）()一-鿿 ]', "_", name).strip() or "上传的表格.xlsx"


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "SkillDistillWeb/1.0"

    # ------------------------------------------------------------ 应答

    def send_json(self, payload, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_bytes(body, "application/json; charset=utf-8", status)

    def send_bytes(self, body: bytes, content_type: str, status: int = 200,
                   filename: str = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            # 中文文件名走 RFC 5987，否则浏览器那边会是乱码
            quoted = "".join(f"%{b:02X}" for b in filename.encode("utf-8"))
            self.send_header("Content-Disposition",
                             f"attachment; filename*=UTF-8''{quoted}")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, message: str, status: int = 400):
        self.send_json({"error": message}, status)

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            raise ValueError(f"请求体超过上限 {MAX_UPLOAD // 1024 // 1024}MB")
        return self.rfile.read(length) if length else b""

    def json_body(self) -> dict:
        body = self.read_body()
        return json.loads(body.decode("utf-8")) if body else {}

    # ------------------------------------------------------------ 路由

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path in ("/", "/index.html"):
                with open(INDEX_PATH, "rb") as f:
                    return self.send_bytes(f.read(), "text/html; charset=utf-8")
            if url.path == "/favicon.ico":
                # 浏览器必定会来要一次，没有就在控制台留一条404，看着像页面报错
                return self.send_bytes(b"", "image/x-icon", 204)
            if url.path == "/api/models":
                return self.send_json({"models": list(MODEL_PROFILES),
                                       "default": DEFAULT_MODEL,
                                       "default_category": DEFAULT_CATEGORY,
                                       "default_output": self.server.default_output})
            if url.path == "/api/skill_file":
                directory = query.get("dir", [""])[0] or self.server.default_output
                rel = query.get("path", [""])[0]
                path = os.path.abspath(os.path.join(
                    os.path.abspath(directory), *rel.split("/")))
                if os.path.commonpath([os.path.abspath(directory), path]) \
                        != os.path.abspath(directory) or not os.path.isfile(path):
                    return self.fail(f"找不到这个 skill: {rel}", 404)
                return self.send_json({"path": rel,
                                       "content": open(path, encoding="utf-8").read()})
            if url.path == "/api/skills":
                return self.send_json(skill_library(
                    query.get("dir", [""])[0] or self.server.default_output))
            if url.path == "/api/job":
                return self.send_json(job_view(get_job(query.get("job", [""])[0])))
            if url.path == "/api/skill":
                job = get_job(query.get("job", [""])[0])
                index = int(query.get("index", ["-1"])[0])
                result = job["results"].get(index)
                if not result or not result.get("content"):
                    return self.fail("这一篇还没有生成内容", 404)
                return self.send_bytes(
                    (result["content"].strip() + "\n").encode("utf-8"),
                    "text/markdown; charset=utf-8",
                    filename=os.path.basename(result["skill_path"]))
            if url.path == "/api/report":
                job = get_job(query.get("job", [""])[0])
                if not job["report"] or not os.path.isfile(job["report"]):
                    return self.fail("这个任务还没有改写说明", 404)
                with open(job["report"], "rb") as f:
                    return self.send_bytes(f.read(), "text/markdown; charset=utf-8",
                                           filename="改写说明.md")
            if url.path == "/api/zip":
                job = get_job(query.get("job", [""])[0])
                indexes = self.indexes_from(job, query.get("indexes", [""])[0])
                return self.send_bytes(make_zip(job, indexes), "application/zip",
                                       filename=f"skills_{job['id'][:8]}.zip")
            return self.fail("没有这个地址", 404)
        except KeyError as e:
            return self.fail(str(e.args[0] if e.args else e), 404)
        except FileNotFoundError:
            return self.fail(f"找不到页面文件 {INDEX_PATH}", 500)
        except Exception as e:
            traceback.print_exc()
            return self.fail(f"{type(e).__name__}: {e}", 500)

    def do_POST(self):
        url = urlparse(self.path)
        try:
            if url.path == "/api/upload":
                return self.handle_upload()
            if url.path == "/api/generate":
                return self.handle_generate()
            if url.path == "/api/edit":
                return self.handle_edit()
            if url.path == "/api/skill_file":
                return self.handle_edit_landed()
            if url.path == "/api/apply":
                return self.handle_apply()
            return self.fail("没有这个地址", 404)
        except KeyError as e:
            return self.fail(str(e.args[0] if e.args else e), 404)
        except ValueError as e:
            return self.fail(str(e), 400)
        except Exception as e:
            traceback.print_exc()
            return self.fail(f"{type(e).__name__}: {e}", 500)

    # ------------------------------------------------------------ 各接口

    def handle_upload(self):
        parts = parse_multipart(self.read_body(), self.headers.get("Content-Type"))
        category = DEFAULT_CATEGORY
        for part in parts:
            if part["name"] == "category" and part["filename"] is None:
                value = part["data"].decode("utf-8", "replace").strip()
                # 空字符串表示"按工作簿文件名分目录"（流水线里的 CATEGORY=None）
                category = value or None
        uploads = [p for p in parts if p["filename"]]
        if not uploads:
            raise ValueError("没有收到任何文件")

        workdir = tempfile.mkdtemp(prefix="skill-web-", dir=self.server.jobs_dir)
        job = new_job(workdir)
        for part in uploads:
            name = safe_filename(part["filename"])
            if not name.lower().endswith(ALLOWED_SUFFIXES):
                shutil.rmtree(workdir, ignore_errors=True)
                raise ValueError(f"只接受 .xlsx / .xlsm，收到的是 {name}")
            with open(os.path.join(workdir, name), "wb") as f:
                f.write(part["data"])
            job["files"].append(name)

        try:
            parse_upload(job, category)
        except Exception as e:
            job["error"] = f"解析失败: {e}"
            return self.fail(f"表格解析失败: {e}")
        print(f"[upload] {job['id'][:8]} {'、'.join(job['files'])} → "
              f"{len(job['scenarios'])} 个场景，{sum(len(i) for _, i in job['audit'])} 处体检问题")
        return self.send_json(job_view(job))

    def handle_generate(self):
        payload = self.json_body()
        job = get_job(payload.get("job"))
        if job["status"] == "running":
            raise ValueError("这个任务正在生成中，等它跑完再提交")
        indexes = self.indexes_from(job, payload.get("indexes"))
        if not indexes:
            raise ValueError("没有选中任何场景")
        model = payload.get("model") or DEFAULT_MODEL
        # 只在这里清掉将要重跑的那几篇，其余保留：一批里失败两篇时，
        # 常见做法是只重跑那两篇，不该把已经生成好的也抹掉
        with JOBS_LOCK:
            for index in indexes:
                job["results"].pop(index, None)
        threading.Thread(
            target=run_generation,
            args=(job, indexes, model, self.server.workers,
                  self.server.max_tokens, self.server.timeout_seconds,
                  self.server.api_url),
            daemon=True).start()
        print(f"[generate] {job['id'][:8]} {len(indexes)} 个场景 @ {model}")
        return self.send_json(job_view(job))

    def handle_edit(self):
        payload = self.json_body()
        job = get_job(payload.get("job"))
        indexes = self.indexes_from(job, [payload.get("index")])
        result = edit_result(job, indexes[0], payload.get("content"))
        print(f"[edit] {job['id'][:8]} 第{indexes[0]}个场景 "
              f"→ {'仍不合规: ' + result['invalid'] if result['invalid'] else '校验通过'}")
        return self.send_json(job_view(job))

    def handle_edit_landed(self):
        payload = self.json_body()
        outcome = edit_landed_skill(
            (payload.get("dir") or "").strip() or self.server.default_output,
            payload.get("path") or "", payload.get("content"),
            force=bool(payload.get("force")))
        print(f"[edit-landed] {outcome['path']}: "
              f"{'已写入' if outcome['saved'] else '有ERROR，未写入'}")
        return self.send_json(outcome)

    def handle_apply(self):
        payload = self.json_body()
        job = get_job(payload.get("job"))
        if job["status"] == "running":
            raise ValueError("这个任务正在生成中，等它跑完再落盘")
        indexes = self.indexes_from(job, payload.get("indexes"))
        output_dir = (payload.get("output_dir") or "").strip() or self.server.default_output
        applied = apply_results(job, indexes, output_dir,
                                force=bool(payload.get("force")))
        print(f"[apply] {job['id'][:8]} → {output_dir}: " +
              "、".join(f"{a['state']} {a.get('path', '')}" for a in applied))
        return self.send_json({"output_dir": os.path.abspath(output_dir),
                               "applied": applied, **job_view(job)})

    def indexes_from(self, job: dict, raw) -> list:
        """把页面传来的场景序号规整成合法序号；不传表示"全部"。"""
        if raw is None or raw == "":
            return list(range(len(job["scenarios"])))
        if isinstance(raw, str):
            raw = [v for v in raw.split(",") if v.strip()]
        indexes = []
        for value in raw:
            index = int(value)
            if not 0 <= index < len(job["scenarios"]):
                raise ValueError(f"场景序号越界: {index}")
            indexes.append(index)
        return sorted(set(indexes))

    def log_message(self, fmt, *args):
        # 默认实现把每个请求都打到 stderr，轮询状态时会把日志刷满
        if self.path.startswith("/api/job"):
            return
        sys.stderr.write(f"{self.log_date_time_string()} {fmt % args}\n")


def main(host: str, port: int, workers: int, output_dir: str,
         max_tokens=None, timeout: int = 600, api_url: str = None):
    jobs_dir = tempfile.mkdtemp(prefix="skill-web-jobs-")
    server = ThreadingHTTPServer((host, port), Handler)
    server.workers = workers
    server.default_output = output_dir
    server.max_tokens = max_tokens
    server.timeout_seconds = timeout
    # 留空时按模型名从 .env 解析地址（与流水线的 API_URL 参数同义）
    server.api_url = api_url
    server.jobs_dir = jobs_dir

    print("=" * 60)
    print("排障步骤表 → skill 网页前端")
    print("=" * 60)
    print(f"地址      : http://{host}:{port}")
    print(f"并发      : {workers}")
    print(f"落盘默认  : {output_dir}")
    print(f"任务临时目录: {jobs_dir}")
    print("生成的内容默认不落盘，页面上确认后再写文件。Ctrl-C 退出。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
        shutil.rmtree(jobs_dir, ignore_errors=True)


def _arg(flag: str, argv: list, default):
    for i, arg in enumerate(argv):
        if arg == flag and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return default


if __name__ == "__main__":
    argv = sys.argv[1:]
    main(
        host=_arg("--host", argv, "127.0.0.1"),
        port=int(_arg("--port", argv, "8000")),
        workers=int(_arg("--workers", argv, "3")),
        # 落盘目录与命令行流水线保持一致，页面上可以改
        output_dir=_arg("--output", argv,
                        f"skills_from_excel/{datetime.now().strftime('%m-%d')}"),
        max_tokens=(int(_arg("--max-tokens", argv, "0")) or None),
        timeout=int(_arg("--timeout", argv, "600")),
        # 指向别的部署时才用；留空按模型名从 .env 取
        api_url=_arg("--api-url", argv, None),
    )
