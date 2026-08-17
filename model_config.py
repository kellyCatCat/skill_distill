#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型接入配置：地址、密钥、输出预算、是否开思考。

地址和密钥放在不入库的 `.env` 里（格式见 `.env.example`），这里只登记每个模型的
调用参数。三条流水线都通过 `skill_self_distill_pipeline.call_model_with_retry`
走到这里，传模型名即可，不必各自记地址。

为什么要按模型分别配 thinking：原先payload里写死了
`chat_template_kwargs={"enable_thinking": False}`——那是给qwen关思考用的，
套到 MiniMax-M2.7-thinking 上会把思考压掉，等于花大模型的钱拿小模型的输出。

用法：
  python3 model_config.py                    # 打印已登记模型的配置（密钥打码），自查用
  python3 model_config.py --probe            # 再向每个模型发一个最小请求，确认链路真的通
  python3 model_config.py <模型名> [--probe]  # 探测指定模型，不必先登记

注意：下面的 MODEL_PROFILES 才是"有哪些模型"的来源，.env 只放地址和密钥。往 .env
里加东西不会让新模型出现在列表里——要么登记进 MODEL_PROFILES，要么用第三种用法
直接按名字探测（没登记的按兜底档处理：QWEN 前缀、不开思考、max_tokens 16384）。
"""
import json
import os
import re
import sys

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

# 每个模型的调用参数。base_url / api_key / max_tokens 从 .env 里按这些环境变量名取。
#
# max_tokens：thinking模型的推理token也算进输出预算，而评测优化要整篇重写
# 近万字符的skill，16384不够用，所以thinking档给到32768。撞上限会报
# finish_reason=length，按报错调大即可。
#
# thinking=False 时才发送关思考的 chat_template_kwargs；thinking=True 时
# 整个字段都不发，让模型按自己的默认行为思考——不猜各家开思考的字段名。
MODEL_PROFILES = {
    "qwen3.6-27b": {
        "env_prefix": "QWEN",
        "thinking": False,
        "max_tokens": 16384,
    },
    # 和 qwen3.6 同一个部署，所以共用 QWEN 前缀。如果它在别的地址，把这里改成
    # .env 里那组变量的前缀（例如 .env 写了 QWEN38_BASE_URL 就填 "QWEN38"）。
    # thinking 按 `python3 model_config.py qwen3.8-27b --probe` 的实测结果填：
    # 回复里出现 reasoning_content 就改成 True，并把 max_tokens 提到 32768。
    "qwen3.8-27b": {
        "env_prefix": "QWEN",
        "thinking": False,
        "max_tokens": 16384,
    },
    # 实测（python3 model_config.py --probe）这个部署的非thinking变体同样返回
    # reasoning_content——关思考的 chat_template_kwargs 在它上面不起作用。既然
    # 发了也没用、而推理照样吃输出预算，就按"会思考"登记：不发那个无效字段，
    # 预算也留够。
    "MiniMax-M2.7": {
        "env_prefix": "MINIMAX",
        "thinking": True,
        "max_tokens": 32768,
    },
    "MiniMax-M2.7-thinking": {
        "env_prefix": "MINIMAX",
        "thinking": True,
        "max_tokens": 32768,
    },
}

# 没登记的模型名按这个兜底，仍可用 api_url 参数直接指定地址
DEFAULT_PROFILE = {"env_prefix": "QWEN", "thinking": False, "max_tokens": 16384}


def load_env_file(path: str = ENV_PATH) -> dict:
    """读 .env（KEY=VALUE，# 开头为注释）。已存在的环境变量优先，不被覆盖。"""
    values = {}
    if not os.path.isfile(path):
        return values
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            # 真实环境变量优先，方便临时覆盖：QWEN_BASE_URL=... python3 xxx.py
            values[key] = os.environ.get(key) or value
    return values


def chat_completions_url(base_url: str) -> str:
    """把 .../v1 补成 .../v1/chat/completions；已经是完整路径的原样返回。"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _allow_direct_connection(url: str) -> None:
    """把模型主机加进 NO_PROXY，避免请求被本机/环境里的代理拦掉。"""
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    if not host:
        return
    for var in ("NO_PROXY", "no_proxy"):
        current = [h for h in os.environ.get(var, "").split(",") if h]
        if host not in current:
            os.environ[var] = ",".join(current + [host])


def resolve_model(model_name: str, api_url: str = None) -> dict:
    """解析出调用一个模型需要的全部参数。

    api_url 显式传入时优先（流水线里旧的 API_URL 参数仍然生效），否则按模型名
    从 .env 里取地址。
    """
    profile = MODEL_PROFILES.get(model_name, DEFAULT_PROFILE)
    env = load_env_file()
    prefix = profile["env_prefix"]

    url = chat_completions_url(api_url or env.get(f"{prefix}_BASE_URL", ""))
    if not url:
        raise ValueError(
            f"模型 {model_name!r} 没有可用地址：请在 .env 里设置 {prefix}_BASE_URL"
            f"（可参考 .env.example），或调用时显式传 api_url")
    _allow_direct_connection(url)

    max_tokens = env.get(f"{prefix}_MAX_TOKENS", "").strip()
    return {
        "model": model_name,
        "api_url": url,
        "api_key": env.get(f"{prefix}_API_KEY", "").strip(),
        "max_tokens": int(max_tokens) if max_tokens.isdigit() else profile["max_tokens"],
        "thinking": profile["thinking"],
        "registered": model_name in MODEL_PROFILES,
    }


def _mask(secret: str) -> str:
    if not secret:
        return "（无，按不鉴权处理）"
    return f"{secret[:7]}…{secret[-4:]}（{len(secret)}字符）"


def _probe_verdict(status: int, content: str, reasoning: str, budget: int,
                   cfg: dict, shape: str) -> str:
    """把探测结果判成 OK / WARN。

    会思考的模型如果预算太小，会把预算全花在推理上、正文一个字都不出——这时链路
    是通的，但报OK会让人误以为回复形态正常，所以单独判WARN并说清原因。
    """
    note = f"，另有 reasoning_content {len(reasoning)}字符" if reasoning else ""
    if content:
        return f"[OK] HTTP {status}，{shape}content={content[:40]!r}{note}"
    if reasoning:
        return (f"[WARN] HTTP {status}，{shape}链路通，但正文为空、只回了推理"
                f"（reasoning_content {len(reasoning)}字符）——探测预算 "
                f"max_tokens={budget} 被思考吃光了。正式调用用的是 "
                f"{cfg['max_tokens']}，够不够要看实跑时有没有报 finish_reason=length")
    return f"[WARN] HTTP {status}，{shape}正文与推理都为空"


def probe(cfg: dict, timeout: int = 120, max_tokens: int = 1024) -> str:
    """发一个小请求，确认这条链路能拿到可用的回复。

    优化流水线一次调用要几分钟，失败后很难分辨是模型输出的问题还是这一层就没通。
    这里快速验证：鉴权对不对、响应体是JSON还是SSE、回复里能不能取到 content。

    预算给到1024而不是十几个token：会思考的模型光推理就能把小预算吃光，那样
    正文永远是空的，探不出链路到底通没通。
    """
    import requests

    payload = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": "回复OK两个字"}],
        "stream": False,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if not cfg["thinking"]:
        payload["chat_template_kwargs"] = {"enable_thinking": False, "thinking": False}
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg["api_key"] else None

    try:
        response = requests.post(cfg["api_url"], json=payload, headers=headers,
                                 timeout=timeout, verify=False)
    except requests.exceptions.RequestException as e:
        return f"[FAIL] 请求发不出去: {type(e).__name__}: {e}"

    content_type = response.headers.get("Content-Type", "?")
    if "text/event-stream" in content_type:
        # 有的端点无视 stream=False 一律返回SSE，这是可以正常工作的形态，不算失败
        response.encoding = "utf-8"
        from skill_self_distill_pipeline import parse_sse_stream
        try:
            content, reasoning, _ = parse_sse_stream(response.text)
        except ValueError as e:
            return f"[FAIL] SSE流解析失败: {e}"
        return _probe_verdict(response.status_code, content.strip(), reasoning,
                              max_tokens, cfg,
                              "SSE流（该端点无视stream=False，已按流式解析），")

    response.encoding = response.encoding or "utf-8"
    body = (response.text or "").strip()
    if response.status_code >= 400:
        return f"[FAIL] HTTP {response.status_code}，正文开头: {body[:200]!r}"
    try:
        res_json = response.json()
    except ValueError:
        detail = "响应体为空" if not body else f"开头: {body[:200]!r}"
        return (f"[FAIL] HTTP {response.status_code} 但响应体不是JSON"
                f"（Content-Type={content_type}，{detail}）")
    try:
        message = res_json["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return f"[FAIL] 回复结构里没有 choices[0].message: {json.dumps(res_json)[:200]}"
    return _probe_verdict(response.status_code, (message.get("content") or "").strip(),
                          message.get("reasoning_content") or "", max_tokens, cfg, "")


def main(do_probe: bool = False, names: list = None):
    print("=" * 72)
    print(f"模型接入配置（.env: {'已读取' if os.path.isfile(ENV_PATH) else '不存在'}）")
    print("=" * 72)

    # 先把 .env 里认出来的前缀列出来。加了新模型却看不到它，多半是因为这里
    # 列的是**代码里登记过的模型**，而 .env 只放地址和密钥、表达不了模型名。
    env = load_env_file()
    suffixes = ("_BASE_URL", "_API_KEY", "_MAX_TOKENS")
    prefixes = sorted({key[:-len(suffix)] for key in env for suffix in suffixes
                       if key.endswith(suffix)})
    if prefixes:
        print(f"\n.env 里的前缀: {'、'.join(prefixes)}")
        unused = [p for p in prefixes
                  if p not in {v["env_prefix"] for v in MODEL_PROFILES.values()}]
        if unused:
            print(f"  [WARN] {'、'.join(unused)} 没有任何模型在用——"
                  f"模型要登记进 model_config.MODEL_PROFILES 才会出现在下面，"
                  f"或者直接 `python3 model_config.py <模型名> --probe` 临时探测")

    for name in (names or list(MODEL_PROFILES)):
        try:
            cfg = resolve_model(name)
        except ValueError as e:
            print(f"\n● {name}\n    [FAIL] {e}")
            continue
        print(f"\n● {name}" + ("" if cfg["registered"] else "（未登记，按兜底配置处理）"))
        print(f"    地址      : {cfg['api_url']}")
        print(f"    密钥      : {_mask(cfg['api_key'])}")
        print(f"    max_tokens: {cfg['max_tokens']}")
        print(f"    思考      : "
              f"{'会思考（不发关思考的字段，预算已留给推理）' if cfg['thinking'] else '关'}")
        if not cfg["registered"]:
            print(f"    说明      : 走的是兜底档（{DEFAULT_PROFILE['env_prefix']} 前缀、"
                  f"不开思考、max_tokens {DEFAULT_PROFILE['max_tokens']}）。"
                  f"地址不对或它会思考的话，要登记进 MODEL_PROFILES")
        if do_probe:
            print(f"    探测      : {probe(cfg)}")
    print(f"\nNO_PROXY: {os.environ.get('NO_PROXY', '（空）')}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    main(do_probe="--probe" in sys.argv[1:], names=args or None)
    sys.exit(0)
