"""知识点卡 → 可执行策略注册表。

每条策略 = 一张知识点卡 + 一个可计算实现（impl 对应 core/engine.py 里的函数）。
参数尽量取自卡片原文的数值判据；没有数值的用保守先验并在 params 里标注来源。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# id, 卡片名, 时段, 展示名, impl, 参数, 说明
STRATS = [
    # ───────────────────────────── 盘前（集合竞价 / 开盘前） ─────────────────────────────
    dict(id="pre_auction_pool", card="竞价强弱判据", bucket="盘前", impl="pre_auction_pool",
         name="竞价强势池",
         params={"gap_min": 1.0, "gap_max": 7.0},
         note="开盘价 >+1% 为强、≤−2% 为弱；取竞价高开 1%~7% 且非一字板"),
    dict(id="pre_first_board_next", card="开盘博弈与首板战法", bucket="盘前", impl="pre_first_board_next",
         name="首板次日高开",
         params={"gap_min": 4.0, "gap_max": 6.0},
         note="一进二高开 4~6% 为硬阈值（知识点-集合竞价入场条件阈值表）"),
    dict(id="pre_gap_volume", card="竞价抢筹模型与抓涨停三战法", bucket="盘前", impl="pre_gap_volume",
         name="竞价抢筹放量",
         params={"vol_ratio_min": 3.0},
         note="教材量比：情绪正常首板 >3 视为强竞价；叠加开盘首根量能"),
    dict(id="pre_mainline_leader", card="轮动行情打法", bucket="盘前", impl="pre_mainline_leader",
         name="主线板块龙头",
         params={"sector_top_pct": 0.2, "rs_min": 80},
         note="只做涨幅前 20% 板块，且个股 RS≥80（行业组与RS分位）"),
    dict(id="pre_limitup_ladder", card="涨停板分时六型", bucket="盘前", impl="pre_limitup_ladder",
         name="昨日涨停梯队",
         params={"min_seal": True},
         note="昨日封板未开 → 次日盘前重点跟踪（分歧转一致）"),
    dict(id="pre_strong_close", card="尾盘模式识别短线择时", bucket="盘前", impl="pre_strong_close",
         name="昨日尾盘拉升",
         params={"tail_min": 0.8},
         note="昨日最后 30 分钟涨幅 ≥0.8% → 主力尾盘抢筹，次日盘前候选"),

    # ───────────────────────────── 盘中 ─────────────────────────────
    dict(id="intra_vwap_support", card="低开高走站上均价", bucket="盘中", impl="intra_vwap_support",
         name="站上分时均价",
         params={"vwap_tol": 0.2},
         note="实时：现价站上分时均价（成交额÷成交量现算），且今日低开或曾下探均价线 → 转强"),
    dict(id="intra_volume_breakout", card="量比变化与综合排名", bucket="盘中", impl="intra_volume_breakout",
         name="盘中放量突破",
         params={"vol_ratio_min": 2.0},
         note="实时：现价刷新日内新高 + 量比 ≥2（或涨速 >0.3%）配合"),
    dict(id="intra_speed_surge", card="分时基础形态与看盘口诀", bucket="盘中", impl="intra_speed_surge",
         name="涨速异动",
         params={"speed_min": 1.2, "bars": 3},
         note="实时：近 5 分钟涨速 ≥1% 且站上分时均价"),
    dict(id="intra_first_touch", card="龙头首触买点", bucket="盘中", impl="intra_first_touch",
         name="10点前首触涨停",
         params={"before": "10:00", "seal_check": True},
         note="实时：用当日 5min K 线定位首触涨停时间 ≤10:00，且当前仍封住"),
    dict(id="intra_sector_flow", card="板块三层结构与龙头指示作用", bucket="盘中", impl="intra_sector_flow",
         name="板块资金流领涨",
         params={"top_n": 5},
         note="实时：行业实时涨幅前 5 的板块中，个股涨幅 ≥3%"),
    dict(id="intra_tail_strong", card="尾盘模式识别短线择时", bucket="盘中", impl="intra_tail_strong",
         name="尾盘强势",
         params={"tail_min": 1.0, "after": "14:30"},
         note="实时：14:30 后现价贴近日内高点且涨速 ≥0（收盘后按收盘快照判定）"),
    dict(id="intra_broken_reseal", card="涨停板分时六型", bucket="盘中", impl="intra_broken_reseal",
         name="炸板回封",
         params={"reseal_window": 30},
         note="实时：当日触板 → 开板 → 当前重新封住（承接强）"),
    dict(id="intra_leader_pullback", card="龙头回踩低吸", bucket="盘中", impl="intra_leader_pullback",
         name="龙头回踩均价",
         params={"vwap_tol": 0.5, "rs_min": 80},
         note="实时：RS≥80（252 日历史 + 现价）且现价贴近均价线不破"),

    # ───────────────────────────── 盘后 ─────────────────────────────
    dict(id="post_rs80", card="RS相对强度≥80", bucket="盘后", impl="post_rs80",
         name="RS≥80 领军股",
         params={"rs_min": 80},
         note="只买 RS≥80（大牛股≥90）；<70 视为落后"),
    dict(id="post_cup_handle", card="带柄茶杯", bucket="盘后", impl="post_cup_handle",
         name="带柄茶杯突破",
         params={"cup_depth_min": 12, "cup_depth_max": 33, "handle_pullback_max": 12, "vol_ratio_min": 1.4},
         note="杯深 12~15%（≤33%）、柄回撤 8~12%；突破柄最高点且量比+40~50%"),
    dict(id="post_55_breakout", card="55日突破", bucket="盘后", impl="post_55_breakout",
         name="55日突破",
         params={"lookback": 55},
         note="收盘突破 55 日最高（唐奇安通道系统1）"),
    dict(id="post_range_volume", card="3点区间放量突破=新行情启动", bucket="盘后", impl="post_range_volume",
         name="窄幅区间放量突破",
         params={"range_max": 3.0, "range_days": 20, "vol_mult": 2.0},
         note="20 日振幅 ≤3%，突然数倍量突破边界 → 新行情启动"),
    dict(id="post_shrink_pullback", card="缩量回踩", bucket="盘后", impl="post_shrink_pullback",
         name="缩量回踩MA20",
         params={"vol_shrink": 0.75, "ma_tol": 3.0},
         note="上升趋势中缩量回踩 MA20（±3%）不破 → 低吸"),
    dict(id="post_volume_divergence", card="量价背离", bucket="盘后", impl="post_volume_divergence",
         name="量价背离预警",
         params={"vol_low": 0.8, "stall_vol": 1.5},
         note="新高缩量 = 上涨乏力；放量滞涨 = 派发嫌疑（作为排除项）"),
    dict(id="post_capital_return", card="资金回流预判", bucket="盘后", impl="post_capital_return",
         name="资金回流预判",
         params={"turnover_max": 6.0, "ma_squeeze_max": 1.5, "score_min": 65},
         note="地量换手<6% + 均线粘合<3% + 底背离 → ≥65 分小仓埋伏"),
    dict(id="post_position", card="位置定多空", bucket="盘后", impl="post_position",
         name="60日低位转强",
         params={"pos_max": 35.0},
         note="位置>形态：60 日高低位置 ≤35% 且站上 MA20"),
    dict(id="post_sector_rs", card="行业组相对强度评级", bucket="盘后", impl="post_sector_rs",
         name="行业组相对强度",
         params={"top_pct": 20},
         note="近 6 月涨幅前 20 组（前 24%）只做前列"),
    dict(id="post_reversal", card="A股动量与反转效应（参数刻度）", bucket="盘后", impl="post_reversal",
         name="短期反转",
         params={"lookback": 20},
         note="A 股短期(1月)反转强：20 日跌幅居前 + 缩量企稳；本地实测 IC +0.04"),
    dict(id="post_box_breakout", card="箱体形态定义与突破本质", bucket="盘后", impl="post_box_breakout",
         name="箱体突破",
         params={"box_days": 40, "box_max": 15},
         note="箱体整理（40 日内振幅 ≤15%）后放量突破上沿"),
    dict(id="post_gap_chain", card="三类缺口信号链（突破-逃逸-衰竭）", bucket="盘后", impl="post_gap_chain",
         name="突破缺口",
         params={"gap_min": 2.0},
         note="向上突破缺口 + 量能配合 → 信号链第一环"),
    dict(id="post_high_vol_leader", card="宽幅波动龙头股优先", bucket="盘后", impl="post_high_vol_leader",
         name="高波动龙头",
         params={"amp_min": 15.0, "rs_min": 80},
         note="波幅 ≥15% 才有钱赚（敏感性指数），叠加龙头 RS"),
]


def load_all_cards():
    p = os.path.join(config.DATA, "cards.json")
    if not os.path.exists(p):
        sys.exit("先运行 ingest/build_cards.py")
    return json.load(open(p, encoding="utf-8"))


def main():
    data = load_all_cards()
    by_name = {c["name"]: c for c in data["cards"]}
    for c in data.get("other_cards", []):
        by_name.setdefault(c["name"], c)

    out, missing = [], []
    for s in STRATS:
        card = by_name.get(s["card"])
        if not card:
            missing.append(s["card"])
            card = {"name": s["card"], "one": "", "body": "", "source": "", "related": [],
                    "tags": [], "thresholds": []}
        item = dict(s)
        item.update({
            "card_one": card.get("one", ""),
            "card_body": card.get("body", "")[:1200],
            "card_source": card.get("source", ""),
            "card_tags": card.get("tags", []),
            "card_thresholds": card.get("thresholds", []),
            "card_in_library": s["card"] in {c["name"] for c in data["cards"]},
        })
        out.append(item)

    buckets = {}
    for s in out:
        buckets.setdefault(s["bucket"], []).append(s["id"])
    payload = {"generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
               "count": len(out), "buckets": buckets, "strategies": out}
    with open(os.path.join(config.DATA, "strategies.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"策略 {len(out)} 条: " + " / ".join(f"{k} {len(v)}" for k, v in buckets.items()))
    if missing:
        print(f"⚠️ 卡片不在库中: {missing}")
    for s in out[:3]:
        print(f"  [{s['bucket']}] {s['id']} <- {s['card']}")


if __name__ == "__main__":
    main()
