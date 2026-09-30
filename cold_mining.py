#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冷门信息挖掘机 / 金融入门挖掘机 —— 服务器版（无 WorkBuddy 依赖）。

每个工作日由 cron 调用：
  python3 cold_mining.py cold      # 冷门领域线
  python3 cold_mining.py finance    # 金融入门线

流程：
  1. 挑一个尚未写过的主题（去重依据 topics_used.json 的稳定 key + 标签相似度；
     候选池用尽则取 topics_pool_extra.json，再尽则由 AI 补题 —— 绝不重复选题）
  2. 若配置了 TAVILY_API_KEY，先调 Tavily 联网搜索该主题的真实资料
  3. 调服务器 AI（Ark glm-5.2, thinking=False）生成完整单文件离线 HTML
     —— 严格沿用 template.html 的暗金样式，4-6 张内联 SVG 线稿，无 emoji，离线可开；
        有检索资料时优先采用并内联标注来源链接
  4. 落盘 reports/<日期>-<slug>.html；更新 index.html 与 topics_log.md
  5. git 提交并推送 GitHub Pages（https://raychan611.github.io/cold-mining/）
  6. 飞书推「主题 + 链接」（webhook 失效时仅记录，不阻断流程）

注意：配置了 TAVILY_API_KEY 时报告基于实时联网检索、可标真实来源；
未配置时回退到模型训练知识，prompt 强制「不确定具体数字就写范围/定性或 [UNSOURCED]」。
服务器经 SSH 部署密钥推送 GitHub（github.com Host 已在 ~/.ssh/config 绑定 cold_mining_deploy 私钥）。
"""
import os
import re
import sys
import json
import time
import difflib
import datetime
import subprocess
import requests
import html as _html_mod
from urllib.parse import unquote

BASE = "/home/ubuntu/cold-mining"

# ---- 1. 在 import ai_client 之前注入环境变量 ----
for _p in ["/home/ubuntu/.server_ai.env", os.path.join(BASE, ".env")]:
    if os.path.exists(_p):
        with open(_p, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())

sys.path.insert(0, "/home/ubuntu/daily-push")
import ai_client
import feishu_notify

TODAY = datetime.date.today().strftime("%Y-%m-%d")
LINE = sys.argv[1] if len(sys.argv) > 1 else "cold"

if LINE == "cold":
    TEMPLATE = os.path.join(BASE, "template.html")
    LOG = os.path.join(BASE, "topics_log.md")
    INDEX = os.path.join(BASE, "index.html")
    REPORTS = os.path.join(BASE, "reports")
    WEBHOOK = os.environ.get("COLD_FEISHU_WEBHOOK", "")
    PREFIX = "【冷门信息挖掘机】"
    LIST_ID = "cold-list"
    REPORT_PREFIX = "reports/"
    SHARE = os.environ.get("COLD_SHARE_BASE", "https://raychan611.github.io/cold-mining/reports/")
    CANDIDATES = [
        ("auction-bargain", "拍卖行捡漏"),
        ("old-camera", "老相机行情"),
        ("stamp-coin", "邮票与钱币收藏"),
        ("vintage-watch", "古董表"),
        ("zisha-teapot", "紫砂壶"),
        ("domain-invest", "域名投资"),
        ("whisky-invest", "威士忌投资"),
        ("sports-card", "球星卡"),
        ("blind-box", "潮玩盲盒"),
        ("wine-futures", "葡萄酒期酒"),
        ("eink-screen", "电子墨水屏"),
        ("mech-keyboard", "机械键盘轴体"),
        ("modular-synth", "模块合成器"),
        ("vinyl-record", "黑胶唱片"),
        ("bonsai", "盆栽与文人树"),
        ("deep-sky", "深空摄影"),
        ("fountain-pen", "钢笔"),
        ("fly-fishing", "路亚钓鱼"),
        ("bird-watch", "观鸟"),
        ("carbon-credit", "碳信用"),
        ("water-rights", "水权"),
        ("fishing-quota", "渔业配额"),
        ("bandwidth-trade", "带宽交易"),
        ("sat-band", "卫星频率"),
        ("rare-earth", "稀土"),
    ]
else:
    TEMPLATE = os.path.join(BASE, "finance", "template.html")
    LOG = os.path.join(BASE, "finance", "topics_log.md")
    INDEX = os.path.join(BASE, "index.html")
    REPORTS = os.path.join(BASE, "finance", "reports")
    INDEX = os.path.join(BASE, "index.html")
    LIST_ID = "finance-list"
    REPORT_PREFIX = "finance/reports/"
    WEBHOOK = os.environ.get("FINANCE_FEISHU_WEBHOOK", "")
    PREFIX = "【金融入门挖掘机】"
    SHARE = os.environ.get("FINANCE_SHARE_BASE", "https://raychan611.github.io/cold-mining/finance/reports/")
    CANDIDATES = [
        ("rule-of-72", "复利与 72 法则"),
        ("inflation", "通货膨胀"),
        ("interest-rate", "利率"),
        ("exchange-rate", "汇率"),
        ("gdp-cpi-ppi", "GDP / CPI / PPI"),
        ("monetary-policy", "货币政策"),
        ("fed", "美联储"),
        ("yield-curve", "收益率曲线"),
        ("credit-spread", "信用利差"),
        ("qe", "量化宽松"),
        ("bond", "债券入门"),
        ("etf", "ETF 入门"),
        ("reit", "REITs 入门"),
        ("gold", "黄金投资"),
        ("commodity", "大宗商品"),
        ("crypto", "加密货币入门"),
        ("market-maker", "做市商"),
        ("stamp-tax", "印花税"),
        ("circuit-breaker", "熔断机制"),
        ("short-selling", "做空"),
        ("option-basics", "期权基础"),
        ("hedge", "对冲"),
        ("leverage", "杠杆"),
        ("pe-pb-roe", "PE / PB / ROE"),
        ("dca", "定投"),
        ("rebalance", "资产配置与再平衡"),
        ("risk-parity", "风险平价"),
        ("tulip-mania", "郁金香狂热"),
        ("south-sea", "南海泡沫"),
        ("black-monday", "黑色星期一"),
        ("index-fund", "指数基金"),
    ]


# ---------------------------------------------------------------------------
# 主题去重（2026-09-30 重写，修复「重复报告」根因）
#
# 旧逻辑的缺陷：
#   1. `if topic not in used` —— 用一个短候选名（如「PE / PB / ROE」）去子串匹配
#      整份日志，而日志里存的是 AI 自己写的**标题**（如「看懂PE、PB、ROE」）。
#      带空格 / 被 AI 改名的主题就永远匹配不上，于是被反复选中（期权基础连发 3 次）。
#   2. 候选池只有 25 / 31 个，用尽后回落到 `toordinal() % len(CANDIDATES)` ——
#      按日历日确定性循环取题，池子一空就开始成批重复。
#
# 新逻辑：
#   ① 以稳定 key 记录已用主题（topics_used.json），不再依赖 AI 标题；
#   ② 归一化标签 + 与历史标签做相似度比对，双重判重（拦近义重复）；
#   ③ 候选池用尽 → 取追加池（topics_pool_extra.json），再尽 → 由 AI 提案新主题
#      并持久化到追加池，可持续扩展；
#   ④ 实在拿不到新主题就当日停发并飞书告警 —— 宁缺毋滥，绝不重复。
# ---------------------------------------------------------------------------
USED_JSON = os.path.join(BASE, "topics_used.json")
EXTRA_POOL = os.path.join(BASE, "topics_pool_extra.json")
SAME_TOPIC_RATIO = 0.78      # 标签相似度 >= 此值视为同一主题
SAME_TITLE_RATIO = 0.85      # 标题相似度 >= 此值视为重复报告（兜底闸门）

_PUNCT_RE = re.compile(r"[\s/\\|·、,，.。:：;；!！?？()（）\[\]【】\"'“”‘’\-—_~～]+")
_LOG_ROW_RE = re.compile(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|\s*(.+?)\s*\|", re.M)


def _norm(s):
    """归一化主题文本：去掉空白、斜杠与各类标点，并小写化。"""
    return _PUNCT_RE.sub("", s or "").lower()


def _log_text():
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8") as f:
            return f.read()
    return ""


def _log_titles():
    return [t.strip() for t in _LOG_ROW_RE.findall(_log_text())]


def _slugs_from_reports():
    """兜底：从 reports 目录文件名反推已用 slug，防日志漏记。"""
    keys = set()
    if os.path.isdir(REPORTS):
        for fn in os.listdir(REPORTS):
            m = re.match(r"\d{4}-\d{2}-\d{2}-(.+?)\.html$", fn)
            if m:
                keys.add(m.group(1))
    return keys


def _load_state():
    """返回 (全部状态, 已用 key 集合, 已用标签列表)。"""
    data = {}
    if os.path.exists(USED_JSON):
        try:
            with open(USED_JSON, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[cold_mining] topics_used.json 损坏，重建: {e!r}")
            data = {}
    node = data.get(LINE) or {}
    keys = set(node.get("keys") or [])
    labels = list(node.get("labels") or [])
    keys |= _slugs_from_reports()
    return data, keys, labels


def _save_state(data, keys, labels):
    data[LINE] = {"keys": sorted(keys), "labels": sorted(set(labels))}
    with open(USED_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)


def _load_extra():
    if os.path.exists(EXTRA_POOL):
        try:
            with open(EXTRA_POOL, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[cold_mining] 追加题池读取失败: {e!r}")
    return {}


def _save_extra(d):
    with open(EXTRA_POOL, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2, sort_keys=True)


def _is_taken(key, label, keys, labels, log_norm, other_labels=()):
    """判重：稳定 key 命中 / 归一化标签命中日志 / 与已用标签相等·包含·高度相似。"""
    if key and key in keys:
        return True
    nl = _norm(label)
    if not nl:
        return True
    if nl in log_norm:
        return True
    for ul in list(labels) + list(other_labels):
        un = _norm(ul)
        if not un:
            continue
        if nl == un or nl in un or un in nl:      # 相等或互为包含（如「陨石」/「陨石收藏」）
            return True
        if difflib.SequenceMatcher(None, nl, un).ratio() >= SAME_TOPIC_RATIO:
            return True
    return False


def _propose_topics(n=12):
    """候选池 + 追加池都用尽时，让 AI 出全新主题，返回 [(slug, label), ...]。"""
    kind = ("冷门小众领域（收藏 / 爱好 / 小众产业 / 另类资产）"
            if LINE == "cold" else "投资理财与经济金融的入门概念")
    prompt = (
        f"我在做一个「{kind}」的每日入门报告系列。已经写过的主题见下"
        f"（**严禁重复，也严禁近义 / 同义重复**）：\n\n{_log_text()[-5000:]}\n\n"
        f"请再给 {n} 个**全新**主题，要求：\n"
        f"1) 真实存在、有料可写，外行能听懂并且会好奇；\n"
        f"2) 彼此之间也不重复，且与上面每一条都明显不同；\n"
        f"3) 不要营销腔、不要鸡汤。\n\n"
        f"输出格式：每行一条，英文小写短横线 slug 与中文标签用竖线分隔，例如：\n"
        f"vintage-poster|老海报收藏\n"
        f"只输出这 {n} 行，不要序号、不要解释、不要标题。"
    )
    try:
        txt = ai_client.chat(prompt, system="你是选题策划，只按给定格式输出，不加任何解释。",
                             temperature=0.95, max_tokens=1500, timeout=180, thinking=False)
    except Exception as e:
        print(f"[cold_mining] AI 提案主题失败: {e!r}")
        return []
    out = []
    for ln in (txt or "").splitlines():
        ln = ln.strip().lstrip("-*#0123456789. \t")
        if "|" not in ln:
            continue
        a, b = ln.split("|", 1)
        a, b = a.strip(), b.strip()
        if re.fullmatch(r"[a-z0-9][a-z0-9\-]{1,60}", a) and b:
            out.append((a, b))
    return out[:n]


def pick_topic():
    """挑一个没写过的主题。返回 (slug, topic)；确实无题可用时返回 (None, None)。"""
    data, keys, labels = _load_state()
    log_norm = _norm(_log_text())
    extra = _load_extra()
    extra_pool = extra.get(LINE) or []
    extra_labels = [lb for _, lb in extra_pool]

    for slug, topic in CANDIDATES:
        if not _is_taken(slug, topic, keys, labels, log_norm, extra_labels):
            return slug, topic

    for slug, topic in extra_pool:
        if not _is_taken(slug, topic, keys, labels, log_norm, ()):
            return slug, topic

    print("[cold_mining] 候选池与追加池均已用尽，改由 AI 追加新主题…")
    fresh, seen = [], set()
    for slug, topic in _propose_topics(12):
        if slug in seen:
            continue
        if not _is_taken(slug, topic, keys, labels, log_norm,
                         extra_labels + [t for _, t in fresh]):
            fresh.append((slug, topic))
            seen.add(slug)
    if fresh:
        extra[LINE] = extra_pool + fresh
        _save_extra(extra)
        print(f"[cold_mining] AI 补充 {len(fresh)} 个新主题，追加池现共 {len(extra[LINE])} 个")
        return fresh[0]

    print("[cold_mining] 拿不到任何未写过的主题，今日停发（宁缺毋滥，绝不重复）")
    return None, None


def parse_output(text):
    slug = None
    title = None
    m = re.search(r"^SLUG:\s*(.+)$", text, re.M)
    if m:
        slug = m.group(1).strip().lower()
    m = re.search(r"^TITLE:\s*(.+)$", text, re.M)
    if m:
        title = m.group(1).strip()
    # 提取完整 HTML
    html = None
    s = text.find("<!DOCTYPE")
    if s == -1:
        s = text.find("<html")
    if s != -1:
        e = text.rfind("</html>")
        if e != -1:
            html = text[s:e + 6]
    return slug, title, html


# 2026-09-04: URL 链接化 + 响应式 CSS 兜底
_SRC_RE = re.compile(r"[(\uFF08]来源[:\uFF1A]\s*<?(\S+?)>?[)\uFF09]")
_RESPONSIVE_CSS = (
    "\n<style id=cold-mining-responsive-20260904>\n"
    "  /* 长 URL 兜底换行（防止撑破容器） */\n"
    "  .card ul li, .card p, .tl-body, .ptxt, .vcell .vb, .sec-note {\n"
    "    overflow-wrap: anywhere;\n"
    "    word-break: break-word;\n"
    "  }\n"
    "  .src { color: #8a8a8a; font-size: 11.5px; text-decoration: none; margin-left: 4px; }\n"
    "  .src:hover { color: #c8a866; text-decoration: underline; }\n"
    "</style>\n"
)


def _url_to_a(match):
    raw = match.group(1).strip("<>").rstrip(".,;:!?)]}\"")
    if not raw.startswith(("http://", "https://")):
        return match.group(0)
    return (
        f'<a class="src" href="{_html_mod.escape(raw)}" target="_blank" '
        f'rel="noopener noreferrer">查看来源</a>'
    )


def _post_process_html(html_text):
    if not html_text:
        return html_text
    if "cold-mining-responsive-20260904" not in html_text:
        if "</head>" in html_text:
            html_text = html_text.replace("</head>", _RESPONSIVE_CSS + "</head>", 1)
        else:
            html_text = html_text.replace("<body>", _RESPONSIVE_CSS + "<body>", 1)
    html_text = _SRC_RE.sub(_url_to_a, html_text)
    return html_text


def web_search(query, api_key, max_results=6):
    """调用 Tavily 搜索，返回拼接的「标题（URL）：摘要」上下文；失败返回空串。"""
    try:
        r = requests.post("https://api.tavily.com/search",
                          json={"api_key": api_key, "query": query,
                                "max_results": max_results, "search_depth": "basic"},
                          timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[cold_mining] tavily error: {e!r}")
        return ""
    items = []
    for it in data.get("results", [])[:max_results]:
        title = it.get("title", "")
        url = it.get("url", "")
        content = (it.get("content") or "")[:280]
        items.append(f"- {title}（{url}）：{content}")
    return "\n".join(items)


def build_prompt(topic, template_text, used_topics, search_context=""):
    if search_context:
        search_block = f"""
## 联网检索到的参考资料（请优先采用，并在正文用「(来源：URL)」内联标注）
{search_context}

要求：上方资料来自实时联网搜索。报告中的具体数字、事实、玩家/平台名称尽量引用这些资料，
并在相关句末标注来源链接，形如 (来源：https://...)。若资料与你的知识冲突，以资料为准；
资料未覆盖之处可用你的知识补充，并标注「据公开资料」或 [UNSOURCED]。
"""
        fact_rule = ("事实优先采用上方检索资料并内联标注来源；检索未覆盖处可用你的知识，"
                     "不确定具体数字写范围/定性或 [UNSOURCED]，不得编造精确值。")
    else:
        search_block = ""
        fact_rule = ("事实来自你的训练知识。对你能确认的数据可给出并标注大致来源"
                     "（如「据行业普遍认知」「公开资料」）；**对不确定的具体数字不要编造精确值**，"
                     "用范围/定性描述，或标注 [UNSOURCED]。")
    prev = [t.strip() for t in _LOG_ROW_RE.findall(used_topics or "")]
    prev_block = ("- 本系列已写过的主题如下，**不得重复、也不得写近义主题**："
                  + "、".join(prev[-60:]) + "\n") if prev else ""
    return f"""你是一位擅长把冷门/专业领域讲给外行听的研究作者。请基于你已有的知识，撰写一份「{topic}」的入门研究报告（中文）。

## 铁律（必须遵守）
- 严格沿用下面给出的模板的整套 CSS 与 HTML 骨架（<style> 与整体结构不要改动），只替换文字内容与示例 SVG。
- 全篇至少 4-6 张「内联 SVG 线稿插画」（单色金 #c8a866 / 白 #e8e8e8，暗底可读），包含：首屏概念图、结构/价值链图、数据图（柱状/对比）、时间轴或误区vs真相对比图。把模板里的示例 SVG 全部替换成该领域相关的真实图形。
- 玩家卡片（section 03）必须用简洁线稿 SVG 小图标（参考模板已有的 4 个图标），严禁用 emoji。
- 全文 emoji 不超过 1-2 个；严禁外链图片或依赖任何网络——必须单文件离线可打开。
- 风格：好奇、白话、带一点幽默但不居高临下；外行读了能懂。
- 保留 <body> 顶部左上角的「返回汇总」悬浮链接（class=to-index，指向 ../index.html），禁止删除或改动它。
{prev_block}- {fact_rule}
- 各 section 都要填真实、具体的内容，不要保留「（示例）」「请替换」之类的占位文字。

## 来源链接格式（2026-09-04 新增）
**所有来源链接必须用 <a> 标签包裹**，形如 <a class="src" href="URL" target="_blank" rel="noopener noreferrer">查看来源</a>，**严禁**写成纯文本「(来源：URL)」。URL 必须是 unquote 后的中文路径，**不要**把 URL-encoded 字符串（%E6%BA%95...）直接贴进正文。

## 输出格式（严格）
第一行：SLUG: <英文短横连字符 slug，用于文件名，如 {topic}>
第二行：TITLE: <中文报告标题>
第三行起：完整 HTML 文档（以 <!DOCTYPE html> 开头，</html> 结尾）。
{search_block}
## 模板（请沿用其样式与骨架）
{template_text}

现在请撰写关于「{topic}」的报告。"""


def update_index(title, slug_file):
    if not os.path.exists(INDEX):
        return
    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    ul_open = f'<ul class="list" id="{LIST_ID}">'
    if ul_open not in html:
        return
    # 复用已有 <li> 的图标 span class，保持样式一致
    seg = html.split(ul_open, 1)[1]
    m = re.search(r'<span class="([^"]+)"', seg)
    cls = m.group(1) if m else "mark"
    icon = ('<svg viewBox="0 0 24 24" fill="none" stroke="#c8a866" stroke-width="1.6">'
            '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>')
    new_li = (f'<li><span class="{cls}">{icon}</span>'
              f'<span class="date">{TODAY}</span>'
              f'<a href="{REPORT_PREFIX}{slug_file}">{title}</a></li>\n')
    html = html.replace(ul_open, ul_open + "\n    " + new_li, 1)
    # 金融线：插入首条后删除空提示（精确匹配完整标签，避免误删嵌套内容）
    html = re.sub(
        r'<p[^>]*class=["\']?empty["\']?[^>]*id=["\']?empty-hint["\']?[^>]*>.*?</p>',
        "", html, flags=re.S | re.I
    )
    with open(INDEX, "w", encoding="utf-8") as f:
        f.write(html)


def append_log(title, slug_file):
    line = f"| {TODAY} | {title} | reports/{slug_file} |\n"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)


def git_push(commit_msg, max_retry=6):
    """提交并推送；GitHub 偶发 TLS 中断时自动重试（必要时先 rebase）。"""
    subprocess.run(["git", "-C", BASE, "add", "-A"], capture_output=True, timeout=60)
    c = subprocess.run(["git", "-C", BASE, "commit", "-m", commit_msg],
                       capture_output=True, text=True, timeout=60)
    if c.returncode != 0:
        print(f"[cold_mining] commit: {(c.stderr.strip() or 'nothing new')[-150:]}")
    for i in range(max_retry):
        r = subprocess.run(["git", "-C", BASE, "push", "origin", "main"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return True, (r.stdout + r.stderr).strip()[-200:]
        print(f"[cold_mining] push attempt {i+1} failed: {(r.stdout + r.stderr).strip()[-150:]}")
        subprocess.run(["git", "-C", BASE, "pull", "--rebase", "origin", "main"],
                       capture_output=True, text=True, timeout=120)
        time.sleep(8)
    return False, "push failed after retries"


def main():
    slug, topic = pick_topic()
    if not topic:
        print(f"[cold_mining] line={LINE} 无新主题，今日停发")
        resp = feishu_notify.send_markdown(
            PREFIX + "今日无新主题，已停发",
            "候选题池与追加池都已用尽，AI 也未能补出新主题。今日不发报告，避免重复。",
            webhook=WEBHOOK)
        sc = resp.get("code", resp.get("StatusCode")) if isinstance(resp, dict) else resp
        print(f"[cold_mining] feishu code={sc}")
        return
    cand_key = slug
    print(f"[cold_mining] line={LINE} topic={topic} key={cand_key}")

    with open(TEMPLATE, encoding="utf-8") as f:
        template_text = f.read()

    used_topics = ""
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8") as f:
            used_topics = f.read()

    # 联网检索（可选）：配置 TAVILY_API_KEY 时先搜真实资料，注入 prompt
    tavily_key = os.environ.get("TAVILY_API_KEY")
    search_context = ""
    if tavily_key:
        q = f"{topic} 是什么 市场规模 价格区间 关键玩家 平台 怎么入门 案例"
        search_context = web_search(q, tavily_key)
        print(f"[cold_mining] tavily hits={len(search_context)}")

    prompt = build_prompt(topic, template_text, used_topics, search_context)
    system = "你是研究作者，产出面向外行的中文入门报告，严格遵循用户给出的模板与格式；有检索资料时优先采用并内联标注来源。"

    text = ""
    for attempt in range(2):
        try:
            text = ai_client.chat(prompt, system=system, temperature=0.9,
                                  max_tokens=9000, timeout=240, thinking=False)
        except Exception as e:
            print(f"[cold_mining] AI error: {e!r}")
            text = ""
        s, t, html = parse_output(text)
        svg_n = html.count("<svg") if html else 0
        print(f"[cold_mining] attempt={attempt} html_len={len(html) if html else 0} svg={svg_n}")
        if html and "<html" in html and svg_n >= 4 and t:
            break
        # 重试：强制更完整
        prompt += "\n\n【重试要求】上一版不合格：必须返回完整 <!DOCTYPE html> 文档，含至少 4 张内联 <svg>，并给出 TITLE 行。"
        text = ""

    if not html or "<html" not in html or not t:
        print("[cold_mining] FAILED: AI 未产出合格 HTML，今日跳过（报告不落盘以免脏数据）")
        return

    # 兜底闸门：标题与历史报告高度相似 → 判为重复，停发（正常不应触发）
    t_norm = _norm(t)
    for prev_title in _log_titles():
        if t_norm and difflib.SequenceMatcher(None, t_norm, _norm(prev_title)).ratio() >= SAME_TITLE_RATIO:
            print(f"[cold_mining] 标题与历史报告高度相似，停发: {t!r} ≈ {prev_title!r}")
            resp = feishu_notify.send_markdown(
                PREFIX + "标题疑似重复，已停发",
                f"今日标题：{t}\n历史相似：{prev_title}\n为避免重复推送，本次不发布。",
                webhook=WEBHOOK)
            sc = resp.get("code", resp.get("StatusCode")) if isinstance(resp, dict) else resp
            print(f"[cold_mining] feishu code={sc}")
            return

    slug = (s or slug or cand_key).strip().lower()
    slug_file = f"{TODAY}-{slug}.html"
    if os.path.exists(os.path.join(REPORTS, slug_file)):       # 防同日同名覆盖
        slug_file = f"{TODAY}-{slug}-{int(time.time()) % 100000}.html"
    out_path = os.path.join(REPORTS, slug_file)
    html = _post_process_html(html)  # 2026-09-04: URL 链接化 + 响应式 CSS
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[cold_mining] wrote {out_path}")

    update_index(t, slug_file)
    append_log(t, slug_file)

    # 记录已用主题：稳定 key（候选 slug + 实际 slug）+ 归一化标签，供日后判重
    data, keys, labels = _load_state()
    keys.add(cand_key)
    keys.add(slug)
    labels.append(topic)
    _save_state(data, keys, labels)
    print(f"[cold_mining] index+log+used 已更新（key={cand_key}）")

    ok, info = git_push(f"add: {LINE} {topic} {TODAY}")
    print(f"[cold_mining] git push ok={ok} info={info}")

    link = SHARE + slug_file
    card_title = PREFIX + t
    resp = feishu_notify.send_markdown(card_title, link, webhook=WEBHOOK)
    sc = resp.get("code", resp.get("StatusCode")) if isinstance(resp, dict) else resp
    print(f"[cold_mining] feishu code={sc} link={link}")
    if sc != 0 and sc is not None:
        print(f"[cold_mining] WARN push failed resp={resp}")


if __name__ == "__main__":
    main()
