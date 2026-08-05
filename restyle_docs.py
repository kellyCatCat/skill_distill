#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调用大模型把排障源文档改写成"分支格式"，同时丢掉回显。

目标格式是一步一个有序列表项、判断分支挂成子项：

    1. **检查接口物理及协议状态**

       在任意视图下执行 `display interface <interface-type> <interface-number>`
       检查入接口是否收到数据报文。
       - 若接口状态为 `Down`，请检查线路连接及接口是否被 `shutdown`。
       - 若接口状态为 `Up` 但无流量，继续后续步骤。

改写交给模型，因为判断分支散在句子里，靠规则只能搬动整行以"若/如果"开头的那几句；
代价是模型必然会顺手改措辞，所以重点全在**把它能改动的范围钉死**——落盘前逐项校验，
不合规就不写文件。想先看清送进模型的到底是什么，用 `--dump`。

回显在**送模型之前就被删掉**：这套文档是拿去蒸馏成skill的，skill 里不留大段回显
（主蒸馏的写作约束也是"不要照搬回显"）。删掉的好处不止是省token——模型看不到的
东西就不可能被它改写或编造。带"回显"标签的围栏块整块删除，命令块保留。

落盘前校验（不合规不写文件，原文保持不变）：
  - 送进去的命令必须一条不少地回来；
  - **不得出现原文没有的命令**——模型编命令是最危险的失真，agent 会照着执行；
  - 每个步骤标题、每个场景标题必须都还在；
  - 围栏必须闭合，篇幅不得缩水到（删完回显的）原文的 70% 以下。

默认不覆盖原文件，改写结果写到另一个目录；`--apply` 才就地覆盖，`--diff` 逐行看
改动（一定不写文件）。

用法：
  python3 restyle_docs.py result/新来源                 # 结果写到 <输入>_branch
  python3 restyle_docs.py result/新来源 out/            # 指定输出目录
  python3 restyle_docs.py result/新来源 --diff          # 只看逐行差异
  python3 restyle_docs.py result/新来源 --apply         # 就地覆盖原文件
  python3 restyle_docs.py result/新来源 --dump          # 只写送模型的中间产物，不调模型
  python3 restyle_docs.py 一篇.md --model MiniMax-M2.7 --workers 2
"""
import difflib
import os
import re
import sys
from multiprocessing import Pool

from model_config import resolve_model
from skill_self_distill_pipeline import call_model_with_retry, extract_markdown_content

# "回显示例："/"**回显**" 这类标签行，它后面紧跟的围栏块整块删掉
ECHO_LABEL = re.compile(r"^[ \t]*(?:\*\*|#{1,6} )?回显(?:示例)?(?:\*\*)?[：:]?[ \t]*$")
FENCE_OPEN = re.compile(r"^[ \t]*(`{3,})")

# 命令是ASCII、正文是中文，一条 display 命令一定在第一个中文字符处结束。
# 字符集里刨掉反引号(0x60)：改写后的命令是 `display xxx` 这种行内代码，带上反引号
# 就会和原文对不上，被误判成"命令丢了"。
DISPLAY_IN_TEXT = re.compile(r"display[ \t][\x20-\x5F\x61-\x7E]*")

# 改写只重排层级，篇幅不该缩水；低于这个比例说明模型概括或漏写了
MIN_KEEP_RATIO = 0.7

PROMPT_TEMPLATE = """你在整理IPRAN网络排障的源文档，需要把下面这篇markdown改写成"分支格式"。这是**排版改写**，不是内容改写。

# 目标格式
每个排障步骤是一个有序列表项：编号 + 加粗的步骤标题，正文缩进在下面，判断分支挂成子项。例如：

```markdown
1. **检查接口物理及协议状态**

   在任意视图下执行 `display interface <interface-type> <interface-number>` 检查入接口是否收到数据报文。若未收到报文，执行 `display ip interface brief` 检查接口状态。
   - 若接口状态为 `Down`，请检查线路连接及接口是否被 `shutdown`。
   - 若接口状态为 `Up` 但无流量，继续后续步骤。

2. **查看BFD会话配置**

   在两端设备任意视图下分别执行：
   1. `display bfd session discriminator <value> verbose`，查看一端的Local Discriminator和对端会话的Remote Discriminator是否一致。
   2. `bfd <sessName>` 进入bfd会话视图，`display this` 查看当前会话配置。
   - 修复建议：两端分别进入BFD视图，修改远端描述符配置为对端BFD会话的本端描述符配置。
```

# 硬性约束（违反任何一条这次改写就作废）
- **命令一个字符都不能改**：命令、参数、字段名、接口名、告警名、视图名一律原样照抄，包括 `<value>`、`[ slot slot-id ]` 这类占位写法。命令用反引号包起来。
- **不得新增原文没有的命令**。原文没写的命令一条都不要补，哪怕你认为这一步应该执行它。
- **不要输出任何回显**。原文里的命令回显已经被删掉了，不要凭记忆补写回显、不要输出大段设备输出样例。
- **步骤一个都不能少**，也不能合并两个步骤。原文的每个步骤标题、每个场景标题都要出现在结果里。
- **不得概括、省略**："其余步骤同上"、"（略）"这类写法一律禁止。
- 描述文字尽量保持原句，只做断句和层级调整：把"若…""如果…""当…"这类判断单独拆成 `- ` 子项；原文里"1、""2、"的子步骤排成有序子项。原文没有明确写出的判断分支不要自己编。
- 修复建议、影响性、修复验证、版本支持这些信息保留下来，挂成步骤的子项（如 `- 修复建议：…`）。
- 保留原文的 `# ` 一级标题和 `## 场景N：…` 小节标题；步骤从 `## 步骤N …` 降级成有序列表项。
- 不要输出frontmatter，不要输出任何解释性文字。

# 待改写的文档
下面 <document> 标签之间的内容是待改写的文档，标签本身不要输出：

<document>
{{DOCUMENT}}
</document>

# 输出格式
只输出一个markdown代码块，内容为改写后的整篇文档：
```markdown
（改写后的全文）
```
"""


def strip_echo_blocks(text: str) -> tuple:
    """删掉"回显"标签及其后紧跟的围栏块，返回 (处理后的文本, 删掉的块数)。

    这套文档是拿去蒸馏成skill的，skill里不留大段回显；删在送模型之前，模型看不到
    也就不会去改写或编造它。只删带回显标签的块，命令块留着。
    """
    lines = text.split("\n")
    kept, dropped, i = [], 0, 0
    while i < len(lines):
        if not ECHO_LABEL.match(lines[i]):
            kept.append(lines[i])
            i += 1
            continue
        # 标签之后允许有空行，再往下必须是围栏才认；否则这行不是回显标签
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        fence = FENCE_OPEN.match(lines[j]) if j < len(lines) else None
        if not fence:
            kept.append(lines[i])
            i += 1
            continue
        closing = re.compile(r"^[ \t]*" + fence.group(1) + r"[ \t]*$")
        j += 1
        while j < len(lines) and not closing.match(lines[j]):
            j += 1
        i = j + 1                      # 跳过标签、空行、整个围栏块
        dropped += 1
        while kept and not kept[-1].strip():
            kept.pop()                 # 顺带收掉标签前面留下的空行
        kept.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip() + "\n", dropped


def _normalize(command: str) -> str:
    """比对命令时忽略空格多少，避免模型把两个空格排成一个就被判成"改了命令"。"""
    return re.sub(r"\s+", " ", command).strip(" \t,;.，。`").lower()


def _substantive(text: str) -> int:
    """实质字符数：汉字与字母数字，不算排版标记。"""
    return len(re.findall(r"[一-鿿A-Za-z0-9]", text or ""))


def commands_in(text: str) -> set:
    """正文里出现的 display 查询命令。

    只认 display 开头的：它们是agent真正要执行的查询，边界也可靠（命令是ASCII、
    正文是中文，一定在第一个中文字符处结束）。配置命令形态太杂，靠篇幅与标题校验兜底。
    """
    return {_normalize(m.group(0)) for m in DISPLAY_IN_TEXT.finditer(text)}


def section_titles(text: str) -> list:
    """改写后必须依然找得到的标题文字：步骤标题与场景标题。"""
    titles = []
    for line in text.split("\n"):
        match = re.match(r"^#{2,4} +(.+?)\s*$", line)
        if not match:
            continue
        # "步骤1 查看BFD会话配置（情形2）" → 取标题文字，编号和情形标记会被改写掉
        title = re.sub(r"^步骤\S*\s*", "", match.group(1))
        title = re.sub(r"（情形\d+）$", "", title).strip()
        if title and title not in ("排障步骤", "排障步骤总览", "排障场景总览"):
            titles.append(title)
    return titles


def check_restyled(new: str, source: str) -> str:
    """改写结果能不能落盘，返回错误说明（空串表示通过）。source 为送模型的文本。"""
    new = (new or "").strip()
    if not new:
        return "模型没有输出内容"

    old_commands, new_commands = commands_in(source), commands_in(new)
    lost = sorted(old_commands - new_commands)
    if lost:
        return f"原文的命令在改写后找不到了: {'、'.join(lost[:5])}"
    # 模型编命令是最危险的失真：agent 会照着执行一条设备上根本没有的命令
    invented = sorted(new_commands - old_commands)
    if invented:
        return f"出现了原文没有的命令: {'、'.join(invented[:5])}"

    lost_titles = [t for t in section_titles(source) if t not in new]
    if lost_titles:
        return f"这些步骤/场景在改写后找不到了: {'、'.join(lost_titles[:5])}"

    if len(re.findall(r"^[ \t]*`{3,}", new, re.MULTILINE)) % 2 != 0:
        return "代码块围栏未闭合，疑似输出被截断"
    # 比的是实质字符（汉字与字母数字），不算 **、`、缩进这些排版符号——换个排版
    # 本来就会增减一堆标记，拿总长度比会把忠实的改写也判失败。实测规则版的忠实
    # 重排能留住98%，所以70%这条线只会拦住真的概括掉内容的输出。
    old_size, new_size = _substantive(source), _substantive(new)
    if new_size < old_size * MIN_KEEP_RATIO:
        return (f"内容缩到原文的{new_size / old_size:.0%}"
                f"（{old_size}→{new_size}个实质字符），疑似概括或漏写")
    return ""


def make_extractor(source: str):
    """内容不合规时抛异常，让 call_model_with_retry 重试并把原因带进最终报错。"""
    def _extractor(text: str) -> str:
        content = extract_markdown_content(text)
        if not content:
            return ""
        error = check_restyled(content, source)
        if error:
            raise ValueError(f"{error}；开头: {content[:120]!r}")
        return content
    return _extractor


def restyle_one(args: tuple) -> dict:
    path, rel, model_name, api_url, max_tokens, timeout = args
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        original = f.read()

    source, dropped = strip_echo_blocks(original)
    print(f"[PID {os.getpid()}] 改写: {rel}（{len(original)}字符 → 删掉 {dropped} 段回显 → "
          f"送模型 {len(source)}字符）")

    reply = call_model_with_retry(
        api_url, model_name, PROMPT_TEMPLATE.replace("{{DOCUMENT}}", source),
        extractor=make_extractor(source), max_tokens=max_tokens, timeout=timeout)
    if reply.startswith("错误："):
        return {"rel": rel, "path": path, "error": reply, "dropped": dropped}

    error = check_restyled(reply, source)
    if error:
        return {"rel": rel, "path": path, "error": f"错误：{error}", "dropped": dropped}
    return {"rel": rel, "path": path, "original": original, "source": source,
            "content": reply.strip() + "\n", "dropped": dropped}


def render_diff(rel: str, old: str, new: str) -> str:
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(),
                                fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="")
    lines = list(diff)
    return "\n".join(f"    {line}" for line in lines) if lines else "    （无差异）"


def collect_markdown(source: str) -> list:
    if os.path.isfile(source):
        return [(source, os.path.basename(source))]
    found = []
    for root, dirs, names in os.walk(source):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(names):
            if name.endswith(".md"):
                path = os.path.join(root, name)
                found.append((path, os.path.relpath(path, source).replace(os.sep, "/")))
    return sorted(found)


def dump_inputs(docs: list, out_dir: str) -> None:
    """只做删回显这一步，把送模型的中间产物写出来，不调模型。

    改写是模型干的，出了问题得先分清是"喂进去的东西不对"还是"模型改坏了"。
    这里把删完回显的文档和拼好的完整prompt都落盘，可以先看清送进去的到底是什么，
    也方便直接拿prompt去别处试。不调模型，所以没配 .env 也能跑。
    """
    print(f"--dump：只写送模型的中间产物到 {out_dir}，不调用模型\n")
    for path, rel in docs:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            original = f.read()
        stripped, dropped = strip_echo_blocks(original)
        prompt = PROMPT_TEMPLATE.replace("{{DOCUMENT}}", stripped)

        doc_path = os.path.join(out_dir, *rel.split("/"))
        prompt_path = doc_path + ".prompt.txt"
        os.makedirs(os.path.dirname(os.path.abspath(doc_path)), exist_ok=True)
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(stripped)
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)
        print(f"[DUMP] {rel}: 原文{len(original)}字符 → 删掉{dropped}段回显 → "
              f"送模型{len(stripped)}字符（prompt共{len(prompt)}字符）")
        print(f"       删完回显的文档: {doc_path}")
        print(f"       完整prompt    : {prompt_path}")
    print(f"\n共 {len(docs)} 篇。确认无误后去掉 --dump 再跑改写。")


def main(source: str, out_dir: str, model_name: str, api_url: str, workers: int,
         apply: bool, show_diff: bool, dump: bool = False,
         max_tokens=None, timeout: int = 600):
    if show_diff or dump:
        apply = False                    # --diff / --dump 都不写原文件
    print("=" * 60)
    print("调用大模型改写排版（分支格式，删除回显）"
          + ("（只看diff，不写文件）" if show_diff else ""))
    print("=" * 60)
    print(f"输入: {source}")

    if not os.path.exists(source):
        print(f"错误: 输入不存在: {source}")
        sys.exit(1)
    docs = collect_markdown(source)
    if not docs:
        print("错误: 没有找到.md文件")
        sys.exit(1)

    if dump:
        print(f"共 {len(docs)} 篇文档\n")
        dump_inputs(docs, out_dir)
        return

    # 先把模型配置解析出来：没配 .env 时在这里明确报错，而不是等并行池里抛栈
    try:
        cfg = resolve_model(model_name, api_url)
    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)
    print(f"模型: {model_name} @ {cfg['api_url']}"
          f"（thinking {'开' if cfg['thinking'] else '关'}，"
          f"max_tokens {max_tokens or cfg['max_tokens']}）")
    print(f"输出: {'就地覆盖原文件' if apply else out_dir}")
    print(f"共 {len(docs)} 篇文档\n")

    task_args = [(path, rel, model_name, api_url, max_tokens, timeout)
                 for path, rel in docs]
    with Pool(processes=min(workers, len(task_args))) as pool:
        results = pool.map(restyle_one, task_args)

    print("\n" + "=" * 60)
    written = 0
    for result in results:
        if result.get("error"):
            print(f"[FAIL] {result['rel']}: {result['error']}")
            continue
        summary = (f"{result['rel']}（{len(result['original'])}→{len(result['content'])}字符，"
                   f"删掉 {result['dropped']} 段回显）")
        if show_diff:
            print(f"[将改写] {summary}\n"
                  + render_diff(result["rel"], result["original"], result["content"]))
            continue
        target = result["path"] if apply else os.path.join(out_dir, *result["rel"].split("/"))
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(result["content"])
        written += 1
        print(f"[{'已覆盖' if apply else '已写入'}] {summary} → {target}")

    failed = [r for r in results if r.get("error")]
    print(f"\n共 {len(results)} 篇，失败 {len(failed)} 篇"
          + ("" if show_diff else f"，写出 {written} 篇"))
    if show_diff:
        print("这是diff预览；确认无误后去掉 --diff 再跑。")
    elif not apply:
        print(f"改写结果在 {out_dir}，原文件未动；确认无误后可直接用这个目录跑蒸馏。")
    if failed:
        print("失败的文档没有写出，原文保持不变，可重跑（模型输出有随机性）。")
        sys.exit(1)


if __name__ == "__main__":
    argv = sys.argv[1:]
    do_apply = "--apply" in argv
    do_diff = "--diff" in argv
    do_dump = "--dump" in argv
    argv = [a for a in argv if a not in ("--apply", "--diff", "--dump")]

    def _option(flag: str, default: str = "") -> str:
        if flag not in argv:
            return default
        idx = argv.index(flag)
        if idx + 1 >= len(argv):
            print(f"错误: {flag} 后面缺少取值")
            sys.exit(1)
        value = argv[idx + 1]
        del argv[idx:idx + 2]
        return value

    model = _option("--model", "qwen3.6-27b")
    worker_count = int(_option("--workers", "3"))
    if not argv:
        print(__doc__)
        sys.exit(1)
    src = argv[0]
    default_out = (os.path.splitext(src)[0] + "_branch" if os.path.isfile(src)
                   else src.rstrip("/") + "_branch")
    main(src, argv[1] if len(argv) > 1 else default_out, model,
         # 地址与密钥按模型名从 .env 解析（见 model_config.py）
         None, worker_count, do_apply, do_diff, do_dump)
