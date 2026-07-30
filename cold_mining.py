#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冷门信息挖掘机 / 金融入门挖掘机 —— 服务器版（无 WorkBuddy 依赖）。

每个工作日由 cron 调用：
  python3 cold_mining.py cold      # 冷门领域线
  python3 cold_mining.py finance    # 金融入门线

流程：
  1. 从候选池挑一个尚未写过的主题（依据 topics_log.md 去重）
  2. 若配置了 TAVILY_API_KEY，先调 Tavily 联网搜索该主题的真实资料
  3. 调服务器 AI（Ark glm-5.2, thinking=False）生成完整单文件离线 HTML
     —— 严格沿用 template.html 的暗金样式，4-6 张内联 SVG 线稿，无 emoji，离线可开；
        有检索资料时优先采用并内联标注来源链接
  4. 落盘 reports/<日期>-<slug>.html；更新 index.html 与 topics_log.md
  5. 本地 git 提交（仅版本留痕，报告由本机直接托管）
  6. 飞书推「主题 + 链接」

注意：配置了 TAVILY_API_KEY 时报告基于实时联网检索、可标真实来源；
未配置时回退到模型训练知识，prompt 强制「不确定具体数字就写范围/定性或 [UNSOURCED]」。
"""
import os
import re
import sys
import time
import datetime
import subprocess
import requests

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
    SHARE = os.environ.get("COLD_SHARE_BASE", "http://82.157.131.241/reports/")
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
    INDEX = os.path.join(BASE, "finance", "index.html")
    REPORTS = os.path.join(BASE, "finance", "reports")
    WEBHOOK = os.environ.get("FINANCE_FEISHU_WEBHOOK", "")
    PREFIX = "【金融入门挖掘机】"
    SHARE = os.environ.get("FINANCE_SHARE_BASE", "http://82.157.131.241/finance/reports/")
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


def pick_topic():
    used = ""
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8") as f:
            used = f.read()
    for slug, topic in CANDIDATES:
        if topic not in used:
            return slug, topic
    # 全写过则循环：取当天序号
    idx = datetime.date.today().toordinal() % len(CANDIDATES)
    return CANDIDATES[idx]


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
    return f"""你是一位擅长把冷门/专业领域讲给外行听的研究作者。请基于你已有的知识，撰写一份「{topic}」的入门研究报告（中文）。

## 铁律（必须遵守）
- 严格沿用下面给出的模板的整套 CSS 与 HTML 骨架（<style> 与整体结构不要改动），只替换文字内容与示例 SVG。
- 全篇至少 4-6 张「内联 SVG 线稿插画」（单色金 #c8a866 / 白 #e8e8e8，暗底可读），包含：首屏概念图、结构/价值链图、数据图（柱状/对比）、时间轴或误区vs真相对比图。把模板里的示例 SVG 全部替换成该领域相关的真实图形。
- 玩家卡片（section 03）必须用简洁线稿 SVG 小图标（参考模板已有的 4 个图标），严禁用 emoji。
- 全文 emoji 不超过 1-2 个；严禁外链图片或依赖任何网络——必须单文件离线可打开。
- 风格：好奇、白话、带一点幽默但不居高临下；外行读了能懂。
- {fact_rule}
- 各 section 都要填真实、具体的内容，不要保留「（示例）」「请替换」之类的占位文字。

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
    ul_open = '<ul class="list" id="report-list">'
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
              f'<a href="reports/{slug_file}">{title}</a></li>\n')
    html = html.replace(ul_open, ul_open + "\n    " + new_li, 1)
    # 金融线：插入首条后删除空提示
    html = re.sub(r'<p class="empty" id="empty-hint">.*?</p>\n?', "", html, flags=re.S)
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
    print(f"[cold_mining] line={LINE} topic={topic} slug={slug}")

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

    slug = s or slug
    slug_file = f"{TODAY}-{slug}.html"
    out_path = os.path.join(REPORTS, slug_file)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[cold_mining] wrote {out_path}")

    update_index(t, slug_file)
    append_log(t, slug_file)
    print(f"[cold_mining] index+log updated")

    if os.environ.get("GITHUB_PUSH") == "1":
        ok, info = git_push(f"add: {LINE} {topic} {TODAY}")
        print(f"[cold_mining] git push ok={ok} info={info}")
    else:
        subprocess.run(["git", "-C", BASE, "add", "-A"], capture_output=True, timeout=60)
        subprocess.run(["git", "-C", BASE, "commit", "-m", f"add: {LINE} {topic} {TODAY}"],
                       capture_output=True, text=True, timeout=60)
        print("[cold_mining] git: 仅本地提交（GitHub 推送已关闭，报告由本机直接托管）")

    link = SHARE + slug_file
    card_title = PREFIX + t
    resp = feishu_notify.send_markdown(card_title, link, webhook=WEBHOOK)
    sc = resp.get("StatusCode") if isinstance(resp, dict) else resp
    print(f"[cold_mining] feishu StatusCode={sc} link={link}")


if __name__ == "__main__":
    main()
