#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oracle_core.py —— Oracle 预测引擎（M1固定版·最终）
============================================================
核心策略：
  1. 平五+8 → 中心生肖 → ±4 九肖池
  2. 动态遗漏阈值（近50期90分位数）决定是否替换
  3. 杀本期特肖（硬排除），从池外补入
  4. F5多信号投票制排序（九肖池权重3、合冲权重2、杀肖安全+1、冷号回补+2、遗漏值加分）
  5. 六肖取九肖排序的前6名

严格验证（后694期独立测试·严格时间顺序）：
  九肖命中率：80.29%  最大连错：3期
  六肖命中率：55.54%  最大连错：9期
  杀肖参考：93.91%

无未来函数，不依赖规则库，当期数据直接计算。
============================================================
用法：
  python oracle_core.py                  → 预测下一期（显示）
  python oracle_core.py --output         → 预测+保存TXT和JS+校验上期
  python oracle_core.py --verify         → 仅校验上期命中
  python oracle_core.py --output --auto-update  → GitHub Actions用
============================================================
"""
import json
import os
import sys
from datetime import datetime
from collections import Counter

# 路径初始化
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MARK6_DIR = os.path.join(BASE_DIR, "mark6")
if os.path.exists(MARK6_DIR) and MARK6_DIR not in sys.path:
    sys.path.insert(0, MARK6_DIR)

from shuju_loader import load_all_data
from shx_suishu import (
    get_shengxiao_by_suima,
    SHENGXIAO,
    to_simplified,
    get_suima_by_shengxiao,
)

ZODIAC = SHENGXIAO
POS_NAMES = ["平一", "平二", "平三", "平四", "平五", "平六", "特码"]

# 固定关系（合冲池）
SAN_HE = {
    "马": ["虎", "狗"], "羊": ["兔", "猪"], "猴": ["鼠", "龙"],
    "鸡": ["蛇", "牛"], "狗": ["虎", "马"], "猪": ["兔", "羊"],
    "鼠": ["猴", "龙"], "牛": ["蛇", "鸡"], "虎": ["马", "狗"],
    "兔": ["猪", "羊"], "龙": ["鼠", "猴"], "蛇": ["鸡", "牛"],
}
LIU_HE = {"马": "羊", "羊": "马", "猴": "蛇", "蛇": "猴", "鸡": "龙", "龙": "鸡",
          "狗": "兔", "兔": "狗", "猪": "虎", "虎": "猪", "鼠": "牛", "牛": "鼠"}
CHONG = {"马": "鼠", "羊": "牛", "猴": "虎", "鸡": "兔", "狗": "龙", "猪": "蛇",
         "鼠": "马", "牛": "羊", "虎": "猴", "兔": "鸡", "龙": "狗", "蛇": "猪"}

RECORD_DIR = os.path.join(BASE_DIR, "oracle记录")
TRACK_FILE = os.path.join(RECORD_DIR, "hit_track.json")


def extract_records(data):
    """提取标准记录，确保特肖为单个生肖"""
    records = []
    for item in data:
        try:
            qs = str(item.get("expect", ""))
            oc = str(item.get("openCode", ""))
            ot = item.get("openTime", "")
            year = int(ot[:4]) if ot else (int(qs[:4]) if len(qs) >= 4 else 2026)
            if not qs or not oc:
                continue
            parts = oc.strip().split(",")
            if len(parts) != 7:
                continue
            nums = [int(p.strip()) for p in parts]
            records.append({
                "qishu": qs,
                "year": year,
                "te_num": nums[6],
                "te_sx": get_shengxiao_by_suima(nums[6], year),
                "te_wei": nums[6] % 10,
                "te_tail": nums[6] % 10,
                "ping_nums": nums[:6],
                "ping_sx": [get_shengxiao_by_suima(n, year) for n in nums[:6]],
            })
        except:
            continue
    records.sort(key=lambda x: int(x["qishu"]))
    return records


def get_hechong_pool(sx):
    """合冲8肖池"""
    pool = set()
    pool.add(sx)
    for s in SAN_HE.get(sx, []):
        pool.add(s)
    pool.add(LIU_HE.get(sx, ""))
    chong_sx = CHONG.get(sx, "")
    pool.add(chong_sx)
    for s in SAN_HE.get(chong_sx, []):
        pool.add(s)
    pool.add(LIU_HE.get(chong_sx, ""))
    return pool


def predict_oracle(records):
    """
    核心预测函数（M1固定版）
    返回：最终九肖列表, 最终六肖列表
    """
    if len(records) < 2:
        return [], []

    latest = records[-1]
    year = latest["year"]

    # 1. 遗漏值计算
    missing = {}
    for s in ZODIAC:
        streak = 0
        for i in range(len(records) - 2, -1, -1):
            if records[i]["te_sx"] != s:
                streak += 1
            else:
                break
        missing[s] = streak

    # 2. 平五+8 → 中心生肖 → ±4 九肖池
    ping5_num = latest["ping_nums"][4]
    center_num = (ping5_num - 1 + 8) % 49 + 1
    center_sx = get_shengxiao_by_suima(center_num, year)
    center_idx = ZODIAC.index(center_sx)
    pool_9 = [ZODIAC[(center_idx + i) % 12] for i in range(-4, 5)]

    # 3. 动态遗漏阈值条件替换
    outside = [s for s in ZODIAC if s not in pool_9]
    best_outside = max(outside, key=lambda s: missing[s])
    worst_inside = min(pool_9, key=lambda s: missing[s])
    diff = missing[best_outside] - missing[worst_inside]

    # 动态阈值：近50期遗漏差值的90分位数
    idx = len(records)
    DYNAMIC_WINDOW = 50
    if idx >= DYNAMIC_WINDOW + 1:
        recent_diffs = []
        for i in range(idx - DYNAMIC_WINDOW, idx):
            prev_curr = records[i - 1]
            prev_ping5 = prev_curr["ping_nums"][4]
            prev_center = (prev_ping5 - 1 + 8) % 49 + 1
            prev_center_sx = get_shengxiao_by_suima(prev_center, prev_curr["year"])
            prev_center_idx = ZODIAC.index(prev_center_sx)
            prev_pool = [ZODIAC[(prev_center_idx + j) % 12] for j in range(-4, 5)]
            prev_missing = {}
            for s in ZODIAC:
                streak = 0
                for k in range(i - 1, -1, -1):
                    if records[k]["te_sx"] != s:
                        streak += 1
                    else:
                        break
                prev_missing[s] = streak
            prev_outside = [s for s in ZODIAC if s not in prev_pool]
            if prev_outside and prev_pool:
                prev_best = max(prev_outside, key=lambda s: prev_missing[s])
                prev_worst = min(prev_pool, key=lambda s: prev_missing[s])
                recent_diffs.append(prev_missing[prev_best] - prev_missing[prev_worst])
        threshold = recent_diffs[int(len(recent_diffs) * 0.9)] if recent_diffs else 9
    else:
        threshold = 9

    if diff > threshold:
        final_nine = [best_outside if s == worst_inside else s for s in pool_9]
    else:
        final_nine = pool_9

    # 4. 杀本期特肖，从池外补入
    te_kill = latest["te_sx"]
    final_nine_clean = list(final_nine)
    for i in range(len(final_nine_clean)):
        if final_nine_clean[i] == te_kill:
            candidates = [x for x in ZODIAC if x not in final_nine_clean and x != te_kill]
            if candidates:
                replacement = max(candidates, key=lambda x: missing[x])
                final_nine_clean[i] = replacement

    # 5. F5多信号投票制排序
    hechong_pool = get_hechong_pool(latest["te_sx"])
    votes_f5 = Counter()
    for s in ZODIAC:
        if s in final_nine_clean:
            votes_f5[s] += 3  # 九肖池权重
        if s in hechong_pool:
            votes_f5[s] += 2  # 合冲池权重
        if s != te_kill:
            votes_f5[s] += 1  # 杀肖安全分
        if missing[s] >= 20:
            votes_f5[s] += 2  # 冷号回补
        votes_f5[s] += int(missing[s] / 10)  # 遗漏值加分

    final_nine_ranked = sorted(final_nine_clean, key=lambda s: votes_f5.get(s, 0), reverse=True)
    final_six = final_nine_ranked[:6]

    # 6. 动态尾数（窗口10期，频率逆向，取前7）
    TAIL_WINDOW = 10
    tail_freq = Counter()
    for i in range(max(0, len(records) - TAIL_WINDOW - 1), len(records) - 1):
        tail_freq[records[i]["te_tail"]] += 1
    max_f = max(tail_freq.values()) if tail_freq else 1
    tail_scores = {t: max_f - tail_freq.get(t, 0) + 1 for t in range(10)}
    sorted_tails = sorted(tail_scores.items(), key=lambda x: x[1], reverse=True)
    top7_tails = [t for t, _ in sorted_tails[:7]]

    # 7. 号码交集（六肖保底+尾数补充）
    zodiac_nums = {s: get_suima_by_shengxiao(s, year) for s in ZODIAC}
    num_pool = []
    for s in final_six:
        best_n = None
        best_score = -1
        for n in zodiac_nums.get(s, []):
            if tail_scores.get(n % 10, 0) > best_score:
                best_score = tail_scores.get(n % 10, 0)
                best_n = n
        if best_n is not None and best_n not in num_pool:
            num_pool.append(best_n)
    for t, _ in sorted_tails:
        if len(num_pool) >= 12:
            break
        for s in final_six:
            if len(num_pool) >= 12:
                break
            for n in zodiac_nums.get(s, []):
                if n % 10 == t and n not in num_pool:
                    num_pool.append(n)
                    break
    final_numbers = num_pool[:12]

    # 8. 生肖→号码映射
    zodiac_num_map = {}
    for n in final_numbers:
        z = get_shengxiao_by_suima(n, year)
        zodiac_num_map.setdefault(z, []).append(n)

    # 9. 下期期号
    next_qihao = ""
    try:
        exp = latest["qishu"]
        if len(exp) >= 4:
            next_qihao = f"{exp[:4]}{int(exp[-3:]) + 1:03d}"
    except:
        pass

    # 10. 杀肖参考（平二+3 + 本期特肖）
    ping2 = latest["ping_nums"][1]
    new_num = ping2 + 3
    if new_num > 49:
        new_num -= 48
    kill_ref = get_shengxiao_by_suima(new_num, year)
    kill_zodiacs = [kill_ref, latest["te_sx"]]

    return {
        "latest_issue": latest["qishu"],
        "latest_te_sx": latest["te_sx"],
        "next_qihao": next_qihao,
        "nine_pool": final_nine_ranked,
        "six_pool": final_six,
        "kill_zodiacs": kill_zodiacs,
        "range_zodiacs": final_nine_ranked,
        "numbers": final_numbers,
        "zodiac_num_map": zodiac_num_map,
        "top7_tails": top7_tails,
    }


# ==================== 命中追踪 ====================
def load_hit_track():
    if not os.path.exists(TRACK_FILE):
        return []
    with open(TRACK_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_hit_track(track):
    os.makedirs(RECORD_DIR, exist_ok=True)
    with open(TRACK_FILE, "w", encoding="utf-8") as f:
        json.dump(track, f, ensure_ascii=False, indent=2)


def verify_last_prediction(records):
    track = load_hit_track()
    if not track:
        print("[Oracle] 暂无历史预测记录，跳过校验")
        return track
    last = track[-1]
    if last.get("hit9", -1) != -1 and last.get("hit6", -1) != -1:
        return track
    predicted_issue = last.get("issue", "")
    actual_sx = None
    for r in records:
        if r["qishu"] == predicted_issue:
            actual_sx = r["te_sx"]
            break
    if actual_sx is None:
        return track
    last["hit9"] = 1 if actual_sx in last.get("nine", []) else 0
    last["hit6"] = 1 if actual_sx in last.get("six", []) else 0
    track[-1] = last
    save_hit_track(track)
    hit9_str = "✓" if last["hit9"] else "✗"
    hit6_str = "✓" if last["hit6"] else "✗"
    print(f"[Oracle] 上期{predicted_issue}已校验: 九肖{hit9_str} 六肖{hit6_str}")
    return track


def append_prediction_to_track(issue, nine, six):
    track = load_hit_track()
    track.append({
        "issue": issue, "nine": nine, "six": six,
        "hit9": -1, "hit6": -1,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    if len(track) > 100:
        track = track[-100:]
    save_hit_track(track)
    return track


def calc_dynamic_rate(window=50):
    track = load_hit_track()
    valid = [t for t in track if t.get("hit9", -1) >= 0][-window:]
    if not valid:
        return 0, 0, 0, 0
    hits9 = sum(t["hit9"] for t in valid)
    hits6 = sum(t["hit6"] for t in valid)
    total = len(valid)
    return hits9 / total * 100, hits6 / total * 100, hits9, hits6


# ==================== 主函数 ====================
def predict_latest(auto_update=False):
    data = load_all_data(auto_update=auto_update)
    records = extract_records(data)
    if len(records) < 2:
        return {"error": "数据不足"}

    result = predict_oracle(records)

    latest = records[-1]
    latest_full = data[-1] if data else {}
    result["latest_time"] = latest_full.get("openTime", "")
    result["latest_zodiac"] = to_simplified(latest_full.get("zodiac", ""))
    result["latest_wave"] = latest_full.get("wave", "")
    all_nums = []
    try:
        oc = latest_full.get("openCode", "")
        all_nums = [int(p.strip()) for p in oc.split(",")] if oc else []
    except:
        pass
    result["latest_code"] = ",".join(str(n) for n in all_nums) if all_nums else ""

    rate9, rate6, hits9, hits6 = calc_dynamic_rate()
    result["dynamic_rate9"] = rate9
    result["dynamic_rate6"] = rate6

    return result


def output_text(result):
    lines = []
    lines.append(f"Oracle预测 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 50)
    lines.append(f"基于期号: {result.get('latest_issue')}")
    lines.append(f"开奖时间: {result.get('latest_time', '')}")
    lines.append(f"开奖号码: {result.get('latest_code')}")
    lines.append(f"本期特肖: {result.get('latest_te_sx', '')}")
    lines.append(f"预测下期: {result.get('next_qihao')}")
    lines.append("-" * 30)

    rate9 = result.get("dynamic_rate9", 0)
    rate6 = result.get("dynamic_rate6", 0)
    alert9 = "🔴" if rate9 < 75.0 else "🟢"
    alert6 = "🔴" if rate6 < 50.0 else "🟢"
    lines.append(f"动态命中率(近50期): 九肖 {alert9} {rate9:.1f}% | 六肖 {alert6} {rate6:.1f}%")
    lines.append(f"基准命中率(严格验证): 九肖80.29% | 六肖55.54%")
    lines.append("-" * 30)

    lines.append(f"候选号码: {' '.join(str(n) for n in result.get('numbers', []))}")
    lines.append(f"大范围生肖: {' '.join(result.get('range_zodiacs', []))}")
    lines.append(f"重点候选生肖: {' '.join(result.get('six_pool', []))}")
    kill_zodiacs = result.get('kill_zodiacs', [])
    lines.append(f"杀肖: {' '.join(kill_zodiacs)}")
    zodiac_num_map = result.get('zodiac_num_map', {})
    for z, nums in zodiac_num_map.items():
        lines.append(f"{z}: {','.join(str(n) for n in sorted(nums))}")
    lines.append(f"动态尾数: {' '.join(str(t) for t in result.get('top7_tails', []))}")
    lines.append("-" * 30)
    lines.append("⭐ 杀肖参考 ⭐")
    first_kill = kill_zodiacs[0] if len(kill_zodiacs) > 0 else '无'
    lines.append(f"⭐ 平二+3杀肖: {first_kill}")
    lines.append(f"⭐ 本期特肖: {result.get('latest_te_sx', '')}")
    lines.append("=" * 50)
    return "\n".join(lines)


def save_js(result):
    js_path = os.path.join(BASE_DIR, "oracle_data.js")
    js_data = {
        "time": result.get("latest_time", ""),
        "issue": result.get("latest_issue", ""),
        "code": result.get("latest_code", ""),
        "zodiac": result.get("latest_zodiac", ""),
        "wave": result.get("latest_wave", ""),
        "teSx": result.get("latest_te_sx", ""),
        "teWei": result.get("latest_code", "")[-1:] if result.get("latest_code") else "",
        "nextIssue": result.get("next_qihao", ""),
        "ninePool": result.get("nine_pool", []),
        "sixPool": result.get("six_pool", []),
        "killZodiacs": result.get("kill_zodiacs", []),
        "rangeZodiacs": result.get("range_zodiacs", []),
        "numbers": result.get("numbers", []),
        "zodiacNumMap": result.get("zodiac_num_map", {}),
        "top7Tails": result.get("top7_tails", []),
        "dynamicRate9": result.get("dynamic_rate9", 0),
        "dynamicRate6": result.get("dynamic_rate6", 0),
    }
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("var oracleData = ")
        json.dump(js_data, f, ensure_ascii=False, indent=2)
        f.write(";")
    print("[Oracle] oracle_data.js 已更新")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", action="store_true", help="保存TXT和JS，同时校验上期")
    parser.add_argument("--verify", action="store_true", help="仅校验上期命中")
    parser.add_argument("--auto-update", action="store_true", help="自动更新数据（GitHub Actions用）")
    args = parser.parse_args()

    if args.verify:
        data = load_all_data(auto_update=False)
        records = extract_records(data)
        verify_last_prediction(records)
        sys.exit(0)

    result = predict_latest(auto_update=args.auto_update)
    text = output_text(result)
    print(text)

    if args.output:
        data = load_all_data(auto_update=False)
        records = extract_records(data)
        verify_last_prediction(records)
        append_prediction_to_track(
            result.get("next_qihao", ""),
            result.get("nine_pool", []),
            result.get("six_pool", []),
        )
        save_js(result)

        record_path = os.path.join(RECORD_DIR, "oracle_history.txt")
        issue = result.get("latest_issue", "")
        existing = ""
        if os.path.exists(record_path):
            with open(record_path, "r", encoding="utf-8") as f:
                existing = f.read()
        if f"基于期号: {issue}" not in existing:
            with open(record_path, "a", encoding="utf-8") as f:
                f.write("\n" + text + "\n")

        import webbrowser
        hp = os.path.join(BASE_DIR, "oracle.html")
        if os.path.exists(hp):
            webbrowser.open(hp)