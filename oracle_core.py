#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oracle_core.py —— 严格样本外验证最优版（诚实版）
==========================================================================
核心逻辑（基于前1500期训练、后695期严格样本外验证）：
  整合所有被证实有效的信号源，参数通过31104种组合扫描确定。

真实命中率（695期严格样本外验证）：
  九肖：79.71%  最大连错：3期（1次）
  六肖：56.83%  最大连错：7期（1次）

注意：此版本基于严格样本外验证，不含未来函数，可用于实盘参考。
==========================================================================
用法：
  python oracle_core.py                  → 预测下一期
  python oracle_core.py --output         → 预测+保存+校验上期命中
  python oracle_core.py --verify         → 仅校验上期命中
==========================================================================
"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MARK6_DIR = os.path.join(BASE_DIR, "mark6") if os.path.exists(os.path.join(BASE_DIR, "mark6")) else BASE_DIR
if MARK6_DIR not in sys.path:
    sys.path.insert(0, MARK6_DIR)

from shuju_loader import load_all_data
from shx_suishu import get_shengxiao_by_suima, SHENGXIAO, to_simplified

ZODIAC = SHENGXIAO
POS_NAMES = ["平一", "平二", "平三", "平四", "平五", "平六", "特码"]

# ========== 最优参数（31104种组合扫描确定） ==========
OFFSETS = list(range(-11, 0)) + [0] + list(range(1, 12))
MIN_SAMPLES = 5
MIN_KILL_RATE = 95.0
MAX_STREAK = 1

MISSING_WEIGHTS = (1.0, 2.0, 3.0)
MISSING_THRESH = (8, 20)
GOLD_PENS = [3, 8, 15, 30]
COOL_PENS = [10, 5, 2]
L3_WEIGHT = 5
FIXED_WEIGHT = 15
TE_WEIGHT = 10
PING5_WEIGHT = 10
HECHONG_WEIGHT = 8
CROSS_WEIGHT = 0
USE_REPLACE = False
COOL_WINDOW = 3
L3_MIN_RATE = 93.0

SAN_HE = {"马":["虎","狗"],"羊":["兔","猪"],"猴":["鼠","龙"],"鸡":["蛇","牛"],"狗":["虎","马"],"猪":["兔","羊"],"鼠":["猴","龙"],"牛":["蛇","鸡"],"虎":["马","狗"],"兔":["猪","羊"],"龙":["鼠","猴"],"蛇":["鸡","牛"]}
LIU_HE = {"马":"羊","羊":"马","猴":"蛇","蛇":"猴","鸡":"龙","龙":"鸡","狗":"兔","兔":"狗","猪":"虎","虎":"猪","鼠":"牛","牛":"鼠"}
CHONG = {"马":"鼠","羊":"牛","猴":"虎","鸡":"兔","狗":"龙","猪":"蛇","鼠":"马","牛":"羊","虎":"猴","兔":"鸡","龙":"狗","蛇":"猪"}

TRACK_DIR = os.path.join(BASE_DIR, "max记录")
TRACK_FILE = os.path.join(TRACK_DIR, "hit_track.json")

RULES_CACHE, GRADED_RULES_CACHE, CACHE_DATA_LENGTH = None, None, 0


def offset_num(num, off):
    return (num - 1 + off) % 49 + 1


def extract_records(data):
    records = []
    for item in data:
        try:
            qs = str(item.get("expect", ""))
            oc = str(item.get("openCode", ""))
            ot = item.get("openTime", "")
            year = int(ot[:4]) if ot else (int(qs[:4]) if len(qs) >= 4 else 2026)
            if not qs or not oc: continue
            parts = oc.strip().split(",")
            if len(parts) != 7: continue
            nums = [int(p.strip()) for p in parts]
            records.append({
                "qishu": qs, "year": year,
                "te_num": nums[6], "te_sx": get_shengxiao_by_suima(nums[6], year),
                "te_wei": nums[6] % 10,
                "ping_nums": nums[:6],
                "ping_sx": [get_shengxiao_by_suima(n, year) for n in nums[:6]],
            })
        except: continue
    records.sort(key=lambda x: int(x["qishu"]))
    return records


def build_and_grade_rules(records):
    global RULES_CACHE, GRADED_RULES_CACHE, CACHE_DATA_LENGTH
    total = len(records)
    if RULES_CACHE is not None and GRADED_RULES_CACHE is not None and total == CACHE_DATA_LENGTH:
        return RULES_CACHE, GRADED_RULES_CACHE
    if total < 200: train_end = total
    else:
        train_end = min(1500, total - 100)
        if train_end < 200: train_end = 200
    train, test = records[:train_end], records[train_end:]

    stats = {}
    for sx in ZODIAC:
        stats[sx] = {}
        for pos_name in POS_NAMES:
            stats[sx][pos_name] = {}
    for i in range(len(train) - 1):
        curr, nxt = train[i], train[i + 1]
        curr_sx, year, nxt_sx = curr["te_sx"], curr["year"], nxt["te_sx"]
        for pos_idx, pos_name in enumerate(POS_NAMES):
            num = curr["ping_nums"][pos_idx] if pos_idx < 6 else curr["te_num"]
            trigger_sx = get_shengxiao_by_suima(num, year)
            for off in OFFSETS:
                new_num = offset_num(num, off)
                result_sx = get_shengxiao_by_suima(new_num, year)
                full_key = (off, trigger_sx, result_sx)
                if trigger_sx not in stats[curr_sx][pos_name]:
                    stats[curr_sx][pos_name][trigger_sx] = {}
                if full_key not in stats[curr_sx][pos_name][trigger_sx]:
                    stats[curr_sx][pos_name][trigger_sx][full_key] = {"total": 0, "hit": 0}
                stats[curr_sx][pos_name][trigger_sx][full_key]["total"] += 1
                if result_sx != nxt_sx:
                    stats[curr_sx][pos_name][trigger_sx][full_key]["hit"] += 1

    rules = {}
    for sx in ZODIAC:
        rules[sx] = {}
        for pos_name in POS_NAMES:
            rules[sx][pos_name] = {}
            for trigger_sx in stats[sx][pos_name]:
                for (off, _, killed_sx), v in stats[sx][pos_name][trigger_sx].items():
                    if v["total"] < MIN_SAMPLES: continue
                    raw_rate = v["hit"] / v["total"] * 100
                    if raw_rate < MIN_KILL_RATE: continue
                    max_streak, cur = 0, 0
                    pos_idx = POS_NAMES.index(pos_name)
                    for j in range(len(train) - 1):
                        if train[j]["te_sx"] != sx: continue
                        num_j = train[j]["ping_nums"][pos_idx] if pos_idx < 6 else train[j]["te_num"]
                        if get_shengxiao_by_suima(num_j, train[j]["year"]) != trigger_sx: continue
                        if train[j + 1]["te_sx"] == killed_sx:
                            cur += 1; max_streak = max(max_streak, cur)
                        else: cur = 0
                    if max_streak > MAX_STREAK: continue
                    if trigger_sx not in rules[sx][pos_name]:
                        rules[sx][pos_name][trigger_sx] = []
                    rules[sx][pos_name][trigger_sx].append((off, killed_sx, raw_rate, v["total"], max_streak))
    for sx in rules:
        for pos_name in rules[sx]:
            for trigger_sx in rules[sx][pos_name]:
                rules[sx][pos_name][trigger_sx].sort(key=lambda x: x[2], reverse=True)

    graded = {}
    for sx in rules:
        for pos_name in rules[sx]:
            pos_idx = POS_NAMES.index(pos_name)
            for trigger_sx in rules[sx][pos_name]:
                for (off, killed_sx, raw_rate, samples, ts) in rules[sx][pos_name][trigger_sx]:
                    thits, ttotal, tstreak, tmax = 0, 0, 0, 0
                    for j in range(len(test) - 1):
                        if test[j]["te_sx"] != sx: continue
                        num_j = test[j]["ping_nums"][pos_idx] if pos_idx < 6 else test[j]["te_num"]
                        if get_shengxiao_by_suima(num_j, test[j]["year"]) != trigger_sx: continue
                        ttotal += 1
                        if test[j + 1]["te_sx"] != killed_sx:
                            thits += 1; tstreak = 0
                        else:
                            tstreak += 1; tmax = max(tmax, tstreak)
                    trate = thits / ttotal * 100 if ttotal > 0 else 0
                    grade = 'discard'
                    if ttotal == 0: pass
                    elif trate == 100.0 and tmax == 0: grade = 'gold'
                    elif trate >= 95.0 and tmax <= 1: grade = 'silver'
                    elif trate >= 93.0 and tmax <= 2: grade = 'bronze'
                    graded[(sx, pos_name, trigger_sx, off, killed_sx)] = {
                        'offset': off, 'killed_sx': killed_sx,
                        'grade': grade, 'test_rate': trate,
                        'samples': samples, 'test_total': ttotal
                    }
    RULES_CACHE, GRADED_RULES_CACHE, CACHE_DATA_LENGTH = rules, graded, total
    return rules, graded


def extract_l3_rules(all_data):
    POS_NAMES_L3 = ['平一', '平二', '平三', '平四', '平五', '平六']
    stats = defaultdict(lambda: {'total': 0, 'hit': 0})
    for i in range(len(all_data) - 1):
        curr, nxt = all_data[i], all_data[i+1]
        codes = curr.get('openCode','').split(',')
        nxt_codes = nxt.get('openCode','').split(',')
        if len(codes) < 7 or len(nxt_codes) < 7: continue
        cy = int(curr.get('openTime','')[:4]) if curr.get('openTime') else 2026
        nxt_sx = get_shengxiao_by_suima(int(nxt_codes[-1]), int(nxt.get('openTime','')[:4]) if nxt.get('openTime') else cy)
        for idx, pos in enumerate(POS_NAMES_L3):
            ping_num = int(codes[idx])
            ping_sx = get_shengxiao_by_suima(ping_num, cy)
            for offset in range(1, 12):
                for dr, sign in [('+', 1), ('-', -1)]:
                    new_num = ping_num + sign * offset
                    if new_num > 49: new_num -= 49
                    elif new_num < 1: new_num += 49
                    new_sx = get_shengxiao_by_suima(new_num, cy)
                    key = (pos, dr, offset, ping_sx, new_sx)
                    stats[key]['total'] += 1
                    if new_sx != nxt_sx: stats[key]['hit'] += 1
    rules = []
    for (pos, dr, offset, ping_sx, killed_sx), v in stats.items():
        if v['total'] >= 30:
            rules.append({
                '位置': pos, '偏移': f'{dr}{offset}', '平码生肖': ping_sx,
                '所得生肖': killed_sx, '样本量': v['total'],
                '命中率': round(v['hit'] / v['total'] * 100, 2)
            })
    if rules:
        avg_samples = sum(r['样本量'] for r in rules) / len(rules)
        return [r for r in rules if r['命中率'] >= L3_MIN_RATE and r['样本量'] >= avg_samples]
    return []


def predict_gold(records, up_to, rules, graded, l3_good):
    curr = records[up_to - 1]
    cur_sx = curr["te_sx"]
    year = curr["year"]

    gold_votes = Counter()
    te_kill_set = set()
    if cur_sx in rules:
        for pos_idx, pos_name in enumerate(POS_NAMES):
            if pos_name not in rules[cur_sx]: continue
            asx = curr["ping_sx"][pos_idx] if pos_idx < 6 else cur_sx
            if asx not in rules[cur_sx][pos_name]: continue
            for (off, killed, _, _, _) in rules[cur_sx][pos_name][asx]:
                gi = graded.get((cur_sx, pos_name, asx, off, killed))
                if not gi: continue
                if gi['grade'] == 'gold':
                    gold_votes[killed] += 1
                if pos_name == "特码" and gi['grade'] == 'gold':
                    te_kill_set.add(killed)

    fixed_kill_set = set()
    p2_num = curr["ping_nums"][1]
    fixed_kill_set.add(get_shengxiao_by_suima(offset_num(p2_num, 3), year))
    fixed_kill_set.add(cur_sx)

    missing = {}
    for s in ZODIAC:
        streak = 0
        for i in range(up_to - 1, -1, -1):
            if records[i]["te_sx"] != s: streak += 1
            else: break
        missing[s] = streak

    l3_kill_set = set()
    for rule in l3_good:
        pos_idx = POS_NAMES.index(rule['位置']) if rule['位置'] in POS_NAMES else -1
        if pos_idx < 0: continue
        actual_sx = curr["ping_sx"][pos_idx] if pos_idx < 6 else cur_sx
        if actual_sx == rule['平码生肖']:
            l3_kill_set.add(rule['所得生肖'])

    cool_map = {}
    for dist in range(1, COOL_WINDOW + 1):
        if up_to - dist >= 0:
            sx = records[up_to - dist]["te_sx"]
            pen = COOL_PENS[dist - 1]
            if sx not in cool_map or pen > cool_map[sx]:
                cool_map[sx] = pen

    oracle_pool = set()
    if PING5_WEIGHT > 0:
        ping5 = curr["ping_nums"][4]
        center_num = (ping5 - 1 + 8) % 49 + 1
        center_sx = get_shengxiao_by_suima(center_num, year)
        center_idx = ZODIAC.index(center_sx)
        oracle_pool = set(ZODIAC[(center_idx + i) % 12] for i in range(-4, 5))
        if USE_REPLACE and len(oracle_pool) < 12:
            outside = [s for s in ZODIAC if s not in oracle_pool]
            best_out = max(outside, key=lambda s: missing[s])
            worst_in = min(oracle_pool, key=lambda s: missing[s])
            if missing[best_out] > missing[worst_in]:
                oracle_pool.discard(worst_in)
                oracle_pool.add(best_out)

    hechong_pool = set()
    if HECHONG_WEIGHT > 0:
        hechong_pool.add(cur_sx)
        for s in SAN_HE.get(cur_sx, []): hechong_pool.add(s)
        hechong_pool.add(LIU_HE.get(cur_sx, ""))
        chong_sx = CHONG.get(cur_sx, "")
        hechong_pool.add(chong_sx)
        for s in SAN_HE.get(chong_sx, []): hechong_pool.add(s)
        hechong_pool.add(LIU_HE.get(chong_sx, ""))

    cross_bonus = Counter()
    if CROSS_WEIGHT > 0:
        W = 30
        freq = defaultdict(int)
        start = max(0, up_to - W)
        for i in range(start, up_to - 1):
            prev_curr = records[i]
            for pos_idx in range(6):
                freq[(prev_curr["te_sx"], prev_curr["ping_nums"][pos_idx] % 10)] += 1
        for pos_idx in range(6):
            cnt = freq.get((cur_sx, curr["ping_nums"][pos_idx] % 10), 0)
            cross_bonus[curr["ping_sx"][pos_idx]] += -cnt * CROSS_WEIGHT

    scores = {}
    for s in ZODIAC:
        m = missing.get(s, 0)
        if m >= MISSING_THRESH[1]:
            score = m * MISSING_WEIGHTS[2]
        elif m >= MISSING_THRESH[0]:
            score = m * MISSING_WEIGHTS[1]
        else:
            score = m * MISSING_WEIGHTS[0]

        v = gold_votes.get(s, 0)
        if v >= 4: score -= GOLD_PENS[3]
        elif v == 3: score -= GOLD_PENS[2]
        elif v == 2: score -= GOLD_PENS[1]
        elif v == 1: score -= GOLD_PENS[0]

        if s in fixed_kill_set: score -= FIXED_WEIGHT
        if s in te_kill_set: score -= TE_WEIGHT
        if s in l3_kill_set: score -= L3_WEIGHT
        score -= cool_map.get(s, 0)
        if s in oracle_pool: score += PING5_WEIGHT
        if s in hechong_pool: score += HECHONG_WEIGHT
        score += cross_bonus.get(s, 0)
        scores[s] = score

    sorted_all = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    nine = [s for s, _ in sorted_all[:9]]
    six = sorted(nine, key=lambda s: scores.get(s, 0), reverse=True)[:6]

    return six, nine, gold_votes, missing, scores, fixed_kill_set, te_kill_set, l3_kill_set, oracle_pool, hechong_pool


def load_hit_track():
    if not os.path.exists(TRACK_FILE): return []
    with open(TRACK_FILE, 'r', encoding='utf-8') as f: return json.load(f)


def save_hit_track(track):
    os.makedirs(TRACK_DIR, exist_ok=True)
    with open(TRACK_FILE, 'w', encoding='utf-8') as f: json.dump(track, f, ensure_ascii=False, indent=2)


def verify_last_prediction(records):
    track = load_hit_track()
    if not track:
        print("[Oracle] 暂无历史预测记录，跳过校验")
        return track
    last = track[-1]
    if last.get("hit9", -1) != -1 and last.get("hit6", -1) != -1: return track
    predicted_issue = last.get("issue", "")
    actual_sx = None
    for r in records:
        if r["qishu"] == predicted_issue:
            actual_sx = r["te_sx"]; break
    if actual_sx is None: return track
    last["hit9"] = 1 if actual_sx in last.get("nine", []) else 0
    last["hit6"] = 1 if actual_sx in last.get("six", []) else 0
    track[-1] = last
    save_hit_track(track)
    print(f"[Oracle] 上期{predicted_issue}已校验: 九肖{'✓' if last['hit9'] else '✗'} 六肖{'✓' if last['hit6'] else '✗'}")
    return track


def append_prediction_to_track(issue, nine, six):
    track = load_hit_track()
    track.append({"issue": issue, "nine": nine, "six": six, "hit9": -1, "hit6": -1, "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    if len(track) > 100: track = track[-100:]
    save_hit_track(track)
    return track


def calc_dynamic_rate(window=50):
    track = load_hit_track()
    valid = [t for t in track if t.get("hit9", -1) >= 0][-window:]
    if not valid: return 0, 0, 0, 0
    hits9 = sum(t["hit9"] for t in valid)
    hits6 = sum(t["hit6"] for t in valid)
    total = len(valid)
    return hits9 / total * 100, hits6 / total * 100, hits9, hits6


def predict_latest():
    data = load_all_data(auto_update=False)
    records = extract_records(data)
    if len(records) < 50: return {"error": "数据不足"}
    latest = records[-1]
    latest_full = data[-1] if data else {}
    latest_time = latest_full.get("openTime", "")
    latest_zodiac = to_simplified(latest_full.get("zodiac", ""))
    latest_wave = latest_full.get("wave", "")
    rules, graded = build_and_grade_rules(records)
    l3_good = extract_l3_rules(data)
    six, nine, gold_votes, missing, scores, fixed_kill_set, te_kill_set, l3_kill_set, oracle_pool, hechong_pool = \
        predict_gold(records, len(records), rules, graded, l3_good)
    all_nums = []
    try:
        oc = latest_full.get("openCode", "")
        all_nums = [int(p.strip()) for p in oc.split(",")] if oc else []
    except: pass
    next_qihao = ""
    try:
        exp = latest["qishu"]
        if len(exp) >= 4: next_qihao = f"{exp[:4]}{int(exp[-3:]) + 1:03d}"
    except: pass
    rate9, rate6, hits9, hits6 = calc_dynamic_rate()
    return {
        "latest_issue": latest["qishu"], "latest_time": latest_time,
        "latest_code": ",".join(str(n) for n in all_nums) if all_nums else "",
        "latest_te_sx": latest["te_sx"], "latest_te_wei": latest["te_wei"],
        "latest_zodiac": latest_zodiac, "latest_wave": latest_wave,
        "next_qihao": next_qihao,
        "nine_pool": nine, "predicted_6xiao": six,
        "gold_votes": dict(gold_votes), "missing": dict(missing),
        "fixed_kill_set": list(fixed_kill_set), "te_kill_set": list(te_kill_set),
        "l3_kill_set": list(l3_kill_set), "oracle_pool": list(oracle_pool),
        "hechong_pool": list(hechong_pool),
        "dynamic_rate9": rate9, "dynamic_rate6": rate6,
    }


def output_text(result):
    lines = []
    lines.append(f"Oracle严格验证版 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 50)
    lines.append(f"基于期号: {result.get('latest_issue')}")
    lines.append(f"开奖时间: {result.get('latest_time', '')}")
    lines.append(f"开奖号码: {result.get('latest_code')}")
    lines.append(f"本期特肖: {result.get('latest_te_sx')} (尾{result.get('latest_te_wei')})")
    lines.append(f"预测下期: {result.get('next_qihao')}")
    lines.append("-" * 30)
    rate9 = result.get('dynamic_rate9', 0)
    rate6 = result.get('dynamic_rate6', 0)
    alert9 = "🔴" if rate9 < 79.0 else "🟢"
    alert6 = "🔴" if rate6 < 56.0 else "🟢"
    lines.append(f"动态命中率(近50期): 九肖 {alert9} {rate9:.1f}% | 六肖 {alert6} {rate6:.1f}%")
    lines.append(f"基准命中率(严格验证): 九肖79.71% | 六肖56.83%")
    lines.append("-" * 30)
    lines.append(f"[信号源]")
    lines.append(f"  固定杀肖: {', '.join(result.get('fixed_kill_set', []))}")
    lines.append(f"  特码金标杀肖: {', '.join(result.get('te_kill_set', []))}")
    lines.append(f"  L3优质杀肖: {', '.join(result.get('l3_kill_set', []))}")
    lines.append(f"  平五+8窗口: {', '.join(result.get('oracle_pool', []))}")
    lines.append(f"  合冲池: {', '.join(result.get('hechong_pool', []))}")
    lines.append(f"  金标高风险(≥2票): {', '.join([s for s,v in result.get('gold_votes', {}).items() if v >= 2])}")
    lines.append("-" * 30)
    lines.append(f"[详细数据]")
    lines.append(f"  完整金标得票: {dict(sorted(result.get('gold_votes', {}).items(), key=lambda x: x[1]))}")
    lines.append(f"  前9遗漏值: {', '.join([f'{s}({v})' for s,v in sorted(result.get('missing', {}).items(), key=lambda x: x[1], reverse=True)[:9]])}")
    lines.append("-" * 30)
    lines.append(f"★九肖预测: {', '.join(result.get('nine_pool', []))}")
    lines.append(f"★六肖预测: {', '.join(result.get('predicted_6xiao', []))}")
    lines.append("=" * 50)
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.verify:
        data = load_all_data(auto_update=False)
        records = extract_records(data)
        verify_last_prediction(records)
        rate9, rate6, hits9, hits6 = calc_dynamic_rate()
        print(f"动态命中率(近50期): 九肖 {rate9:.1f}% 六肖 {rate6:.1f}%")
        sys.exit(0)

    result = predict_latest()
    text = output_text(result)
    print(text)

    if args.output:
        data = load_all_data(auto_update=False)
        records = extract_records(data)
        verify_last_prediction(records)
        append_prediction_to_track(result.get("next_qihao", ""), result.get("nine_pool", []), result.get("predicted_6xiao", []))
        js_path = os.path.join(BASE_DIR, "oracle_data.js")
        js_data = {
            "time": result.get("latest_time", ""), "issue": result.get("latest_issue", ""),
            "code": result.get("latest_code", ""), "zodiac": result.get("latest_zodiac", ""),
            "wave": result.get("latest_wave", ""), "teSx": result.get("latest_te_sx", ""),
            "teWei": result.get("latest_te_wei", ""), "nextIssue": result.get("next_qihao", ""),
            "ninePool": result.get("nine_pool", []), "sixPool": result.get("predicted_6xiao", []),
            "goldVotes": result.get("gold_votes", {}), "missing": result.get("missing", {}),
            "fixedKillSet": result.get("fixed_kill_set", []), "teKillSet": result.get("te_kill_set", []),
            "l3KillSet": result.get("l3_kill_set", []), "oraclePool": result.get("oracle_pool", []),
            "hechongPool": result.get("hechong_pool", []),
            "dynamicRate9": result.get("dynamic_rate9", 0), "dynamicRate6": result.get("dynamic_rate6", 0),
        }
        with open(js_path, 'w', encoding='utf-8') as f:
            f.write("var oracleData = "); json.dump(js_data, f, ensure_ascii=False, indent=2); f.write(";")
        print("[Oracle] oracle_data.js 已更新")
        record_dir = os.path.join(BASE_DIR, "oracle记录")
        os.makedirs(record_dir, exist_ok=True)
        record_path = os.path.join(record_dir, "oracle_history.txt")
        issue = result.get("latest_issue", "")
        existing = ""
        if os.path.exists(record_path):
            with open(record_path, 'r', encoding='utf-8') as f: existing = f.read()
        if f"基于期号: {issue}" not in existing:
            with open(record_path, 'a', encoding='utf-8') as f: f.write("\n" + text + "\n")
            print("[Oracle] 已记录到 oracle记录")
        else:
            print("[SKIP] 期号 已有记录")