"""扫描 Obsidian 知识库 → data/cards.json

产出：每张「选股相关」知识点卡 = 一条策略条目。
字段包含：所属时段（盘前/盘中/盘后）、可执行性、数值判据、原文摘要、双链。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# ── 时段关键词（标题/一句话权重 3，正文权重 1）────────────────────────────
# 强信号（标题/一句话里出现即基本定性）
PRE_STRONG = ["集合竞价", "竞价", "09:15", "9:15", "09:20", "9:20", "09:25", "9:25",
              "盘前", "隔夜", "首板次日", "竞价额", "竞价量", "开盘区间", "开盘价"]
PRE_WEAK = ["开盘", "高开", "低开", "缺口"]
INTRA_STRONG = ["分时", "盘中", "日内", "涨速", "量比", "盘口", "做t", "做T", "均价线",
                "首触", "封板", "开板", "炸板", "涨停时间", "半路", "打板", "尾盘"]
INTRA_WEAK = ["白线", "黄线", "跳水", "拉升", "承接", "买一", "卖一", "封单", "10:00", "10点"]

# ── 策略类型标签 ─────────────────────────────────────────────────────────
TAG_RULES = [
    ("竞价", ["竞价", "开盘价", "高开", "低开", "集合竞价"]),
    ("分时", ["分时", "均价线", "白线", "黄线", "日内", "做t", "做T"]),
    ("涨停/打板", ["涨停", "封板", "连板", "首板", "炸板", "打板", "接力"]),
    ("量价", ["量价", "放量", "缩量", "成交量", "量比", "倍量", "天量", "地量"]),
    ("形态", ["形态", "突破", "杯柄", "旗形", "三角", "头肩", "箱体", "平台", "缺口", "回踩", "支撑", "阻力"]),
    ("均线", ["均线", "ma5", "ma10", "ma20", "ma60", "多头排列", "粘合"]),
    ("指标", ["macd", "rsi", "kdj", "boll", "布林", "atr", "obv", "指标"]),
    ("资金", ["资金流", "主力", "净流入", "龙虎榜", "机构", "北向", "换手"]),
    ("板块轮动", ["板块", "行业", "轮动", "题材", "主线", "龙头"]),
    ("基本面", ["eps", "roe", "业绩", "财报", "营收", "净利", "估值", "pe", "pb", "canslim"]),
    ("大盘择时", ["大盘", "指数", "趋势", "择时", "环境", "轮动指数"]),
    ("风控", ["止损", "止盈", "仓位", "回撤", "风险"]),
    ("方法/统计", ["回测", "统计", "蒙特卡洛", "因子", "显著性", "样本", "偏差", "概率", "期望"]),
]

# 选股范畴：
#   核心 = 诉求-01（选股）
#   补充 = 诉求-02（买入判断）中属于盘前/盘中的买点、竞价卡
#   环境 = 诉求-07（大盘择时）中讲轮动/大盘环境的卡（作为推荐闸门，不单独选股）
DEMAND_CORE = "诉求-01"
DEMAND_BUY = "诉求-02"
DEMAND_ENV = "诉求-07"


def parse_cards():
    if not os.path.isdir(config.VAULT):
        sys.exit(f"❌ 知识库不存在: {config.VAULT}")
    cards = []
    for fn in sorted(os.listdir(config.VAULT)):
        if not fn.startswith("知识点-") or not fn.endswith(".md"):
            continue
        path = os.path.join(config.VAULT, fn)
        txt = open(path, encoding="utf-8").read()
        name = fn[len("知识点-"):-3]

        def sect(tag):
            m = re.search(rf"##\s*{tag}\s*\n(.*?)(?=\n##\s|\Z)", txt, re.S)
            return m.group(1).strip() if m else ""

        one = sect("一句话")
        body = (sect("操作要点") + "\n" + sect("A股落地")).strip()
        rel = re.findall(r"\[\[知识点-([^\]]+)\]\]", txt)
        # 场景只认 `> 诉求：` 行
        scene_line = " ".join(l for l in txt.splitlines() if l.strip().startswith("> 诉求"))
        demands = sorted(set(re.findall(r"诉求-\d+", scene_line)))
        src = ""
        for l in txt.splitlines():
            if l.strip().startswith("> 来源"):
                src = l.strip().lstrip("> ").replace("来源：", "").replace("来源:", "").strip()
                break

        hay_hot = (name + " " + one).lower()
        hay_body = body.lower()
        hit_pre = [k for k in PRE_STRONG if k.lower() in hay_hot] + \
                  [k for k in PRE_WEAK if k.lower() in hay_hot]
        hit_intra = [k for k in INTRA_STRONG if k.lower() in hay_hot] + \
                    [k for k in INTRA_WEAK if k.lower() in hay_hot]
        pre = 5 * sum(1 for k in PRE_STRONG if k.lower() in hay_hot) + \
              2 * sum(1 for k in PRE_WEAK if k.lower() in hay_hot) + \
              sum(1 for k in PRE_STRONG + PRE_WEAK if k.lower() in hay_body)
        intra = 5 * sum(1 for k in INTRA_STRONG if k.lower() in hay_hot) + \
                2 * sum(1 for k in INTRA_WEAK if k.lower() in hay_hot) + \
                sum(1 for k in INTRA_STRONG + INTRA_WEAK if k.lower() in hay_body)

        # 判定：标题/一句话里有强信号才改判，否则默认盘后（收盘后研究选股）
        bucket = "盘后"
        reason = []
        if hit_pre and pre > intra:
            bucket = "盘前"; reason = hit_pre[:4]
        elif hit_intra and intra > pre:
            bucket = "盘中"; reason = hit_intra[:4]

        tags = [t for t, kws in TAG_RULES
                if any(k.lower() in hay_hot or k.lower() in hay_body for k in kws)]

        # 数值判据抽取（如 ≥18~20%、<6%、1.5 倍、5 日）
        nums = re.findall(r"[≥≤<>＞＜]?\s*\d+(?:\.\d+)?\s*(?:~|-)?\s*\d*(?:\.\d+)?\s*(?:%|倍|日|天|周|月|分|点|亿|万)", body)
        nums = list(dict.fromkeys(n.strip() for n in nums))[:12]

        cards.append({
            "name": name,
            "file": fn,
            "demands": demands,
            "select_related": bool(set(demands) & {DEMAND_CORE, DEMAND_BUY, DEMAND_ENV}),
            "bucket": bucket,
            "bucket_scores": {"盘前": pre, "盘中": intra},
            "bucket_reason": reason,
            "tags": tags,
            "one": one,
            "body": body,
            "source": src,
            "related": [r for r in dict.fromkeys(rel) if r != name],
            "thresholds": nums,
        })
    return cards


PLAYBOOK = "作战手册-01-选股.md"


def load_playbook():
    p = os.path.join(config.VAULT, PLAYBOOK)
    if not os.path.exists(p):
        return None
    return {"file": PLAYBOOK, "title": "作战手册 · 诉求-01 选股", "markdown": open(p, encoding="utf-8").read()}


def main():
    cards = parse_cards()
    for c in cards:
        c["name"] = c["name"].strip("-").strip()

    sel = []
    for c in cards:
        d = set(c["demands"])
        cat = None
        if DEMAND_CORE in d:
            cat = "选股"
        elif DEMAND_BUY in d and c["bucket"] in ("盘前", "盘中"):
            cat = "买点"
        elif (DEMAND_ENV in d
              and any(k in c["name"] for k in ["轮动", "大盘", "指数", "环境", "择时", "情绪", "三重运动"])
              and not any(k in c["name"] for k in ["崩盘", "止损", "心理", "仓位", "卖出", "纪律"])):
            cat = "环境"
        if cat:
            c["category"] = cat
            c["is_strategy"] = True
            sel.append(c)
    keys = {c["name"] for c in sel}
    others = [c for c in cards if c["name"] not in keys]
    out = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "vault": config.VAULT,
        "total_cards": len(cards),
        "selection_cards": len(sel),
        "cards": sel,
        "other_cards": [{"name": c["name"], "bucket": c["bucket"], "tags": c["tags"]} for c in others],
        "playbook": load_playbook(),
        "stats": {
            "by_bucket": {b: sum(1 for c in sel if c["bucket"] == b) for b in ["盘前", "盘中", "盘后"]},
            "by_category": {k: sum(1 for c in sel if c.get("category") == k) for k in ["选股", "买点", "环境"]},
            "by_tag": {t: sum(1 for c in sel if t in c["tags"]) for t, _ in TAG_RULES},
        },
    }
    os.makedirs(config.DATA, exist_ok=True)
    with open(os.path.join(config.DATA, "cards.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"知识点卡总数: {len(cards)}")
    print(f"选股相关: {len(sel)}")
    print(f"类别分布: {out['stats']['by_category']}")
    print(f"时段分布: {out['stats']['by_bucket']}")
    print(f"类型分布: {out['stats']['by_tag']}")
    print("样例:")
    for c in sel[:6]:
        print(f"  [{c['bucket']}] {c['name']}  诉求={c['demands']} 标签={c['tags'][:3]}")


if __name__ == "__main__":
    main()
