#!/usr/bin/env python3
"""
自动抓取杭州徒步高赞路线，更新 routes.json
通过搜索引擎间接获取小红书公开内容，避免直接爬取
每周由 GitHub Actions 定时运行
"""
import json
import re
import requests
from bs4 import BeautifulSoup
from datetime import date
import hashlib
import time
import os

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

# 杭州徒步地点库，用于验证提取的路线是否靠谱
HZ_LOCATIONS = {
    '九溪','九溪烟树','龙井','龙井村','十里琅珰','梅家坞','宝石山','北高峰',
    '灵隐寺','韬光寺','玉皇山','凤凰山','老和山','吴山广场','吴山',
    '万松书院','湘湖','西湖','断桥','苏堤','白堤',
    '飞来峰','云栖竹径','五云山','虎跑','六和塔',
    '南屏山','雷峰塔','花港观鱼','太子湾','岳庙','岳王庙',
    '黄龙洞','翠竹千竿','抱朴道院','保俶塔','葛岭',
    '八卦田','净慈寺','紫来洞','植物园','青芝坞',
    '茅家埠','浴鹄湾','乌龟潭','杭州花圃','郭庄',
    '柳浪闻莺','九曜山','初阳台','曲院风荷','法喜寺',
    '天竺','满觉陇','杨梅岭','翁家山','龙井问茶',
    '棋盘山','美人峰','桃源岭','午潮山','大清谷',
    '理安寺','财神庙','灵顺寺','梅灵隧道','少年宫',
    '保俶路','栖霞岭','紫云洞','玉泉','三天竺',
    '上天竺','中天竺','下天竺','法镜寺','法净寺',
    '凤篁岭','狮峰','南山路','虎跑路','九溪公交站',
}

# 路线封面配色模板
COVER_STYLES = [
    {'bg': 'linear-gradient(135deg,#4facfe,#00f2fe)', 'icon': '🌿'},
    {'bg': 'linear-gradient(135deg,#a18cd1,#fbc2eb)', 'icon': '⛰️'},
    {'bg': 'linear-gradient(135deg,#ff9a56,#ff6a00)', 'icon': '🙏'},
    {'bg': 'linear-gradient(135deg,#30cfd0,#330867)', 'icon': '🥾'},
    {'bg': 'linear-gradient(135deg,#11998e,#38ef7d)', 'icon': '🏯'},
    {'bg': 'linear-gradient(135deg,#89f7fe,#66a6ff)', 'icon': '🚣'},
    {'bg': 'linear-gradient(135deg,#f093fb,#f5576c)', 'icon': '🌸'},
    {'bg': 'linear-gradient(135deg,#ffecd2,#fcb69f)', 'icon': '🍂'},
]


def search_duckduckgo(query, max_results=15):
    """通过 DuckDuckGo HTML 版搜索"""
    results = []
    try:
        url = 'https://html.duckduckgo.com/html/'
        resp = requests.post(url, data={'q': query}, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        for item in soup.select('.result'):
            title_el = item.select_one('.result__title a')
            snippet_el = item.select_one('.result__snippet')
            if title_el and snippet_el:
                results.append({
                    'title': title_el.get_text(strip=True),
                    'snippet': snippet_el.get_text(strip=True),
                })
    except Exception as e:
        print(f"  [!] DuckDuckGo 搜索失败: {e}")
    return results[:max_results]


def search_bing(query, max_results=15):
    """通过 Bing 搜索作为备用"""
    results = []
    try:
        url = f'https://www.bing.com/search?q={requests.utils.quote(query)}&count=20'
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        for item in soup.select('.b_algo'):
            title_el = item.select_one('h2 a')
            snippet_el = item.select_one('.b_caption p')
            if title_el and snippet_el:
                results.append({
                    'title': title_el.get_text(strip=True),
                    'snippet': snippet_el.get_text(strip=True),
                })
    except Exception as e:
        print(f"  [!] Bing 搜索失败: {e}")
    return results[:max_results]


def extract_waypoints(text):
    """从文本中提取路线节点"""
    best = []
    # 尝试不同的分隔符
    for sep in ['→', '➡️', '➡', '—', '－', '﹣', '-', '–', '|', '~', '到']:
        if sep not in text:
            continue
        parts = [p.strip().strip('。，,.!！?？ ') for p in text.split(sep)]
        # 过滤：2-10个中文字符，像地名
        locs = [p for p in parts if 2 <= len(p) <= 10 and re.search(r'[\u4e00-\u9fff]{2,}', p)]
        if len(locs) >= 2:
            # 验证至少一个是已知杭州地点
            matched = sum(1 for loc in locs if any(hz in loc or loc in hz for hz in HZ_LOCATIONS))
            if matched >= 1 and len(locs) > len(best):
                best = locs
    return best


def extract_meta(text):
    """提取距离、时间、爬升等信息"""
    parts = []
    dist = re.search(r'(\d+\.?\d*)\s*(km|公里|千米)', text, re.I)
    if dist:
        parts.append(f"{dist.group(1)}km")
    t = re.search(r'约?(\d+\.?\d*)\s*(小时|h)', text, re.I)
    if t:
        parts.append(f"约{t.group(1)}小时")
    elev = re.search(r'爬升\s*(\d+)\s*m', text, re.I)
    if elev:
        parts.append(f"爬升{elev.group(1)}m")
    return ' · '.join(parts)


def extract_likes(text):
    """尝试提取点赞数"""
    m = re.search(r'(\d+\.?\d*)\s*[wW万]', text)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r'(\d+\.?\d*)\s*[kK千]', text)
    if m:
        return int(float(m.group(1)) * 1000)
    m = re.search(r'(\d{3,})\s*(赞|点赞|喜欢|收藏)', text)
    if m:
        return int(m.group(1))
    return 0


def clean_route_name(title):
    """清理标题，生成简洁的路线名"""
    # 去掉常见后缀噪音
    name = re.sub(r'[|｜\-—].*?(小红书|攻略|分享|推荐|笔记|详细|保姆级|收藏).*$', '', title).strip()
    name = re.sub(r'(超详细|保姆级|史上最全|必看|建议收藏|强推|绝绝子|yyds).*$', '', name).strip()
    name = re.sub(r'^\d+\.\s*', '', name)  # 去掉开头数字编号
    # 截断
    if len(name) > 18:
        name = name[:18]
    return name.strip()


def make_id(name):
    """根据名称生成稳定 ID"""
    return hashlib.md5(name.encode()).hexdigest()[:12]


def search_routes():
    """搜索并提取路线"""
    queries = [
        '杭州徒步路线推荐 site:xiaohongshu.com',
        '杭州登山路线 高赞 site:xiaohongshu.com',
        '杭州西湖群山徒步 site:xiaohongshu.com',
        '杭州爬山攻略 路线 site:xiaohongshu.com',
    ]

    all_routes = []
    seen = set()

    for query in queries:
        print(f"\n🔍 搜索: {query}")
        # 先试 DuckDuckGo，失败再试 Bing
        results = search_duckduckgo(query)
        if not results:
            print("  DuckDuckGo 无结果，尝试 Bing...")
            results = search_bing(query)
        print(f"  获取到 {len(results)} 条结果")

        for r in results:
            full = r['title'] + ' ' + r['snippet']

            # 提取路线节点
            waypoints = extract_waypoints(full)
            if not waypoints or len(waypoints) < 2:
                continue

            # 清理路线名
            name = clean_route_name(r['title'])
            if not name or name in seen:
                continue
            seen.add(name)

            # 提取元信息
            meta = extract_meta(full)
            if not meta:
                # 根据节点数估算
                km = len(waypoints) * 2
                hours = max(1, len(waypoints) * 0.5)
                meta = f"{km}km · 约{hours:.1f}小时"

            likes = extract_likes(full)
            if likes == 0:
                # 搜索结果靠前的一般比较热门
                likes = max(3000, 8000 - len(all_routes) * 500)

            route_type = 'hiking'
            if any(kw in full for kw in ['休闲', '散步', '环湖', '平路', '亲子']):
                route_type = 'leisure'

            desc = r['snippet'][:80]

            route = {
                'id': make_id(name),
                'name': name,
                'type': route_type,
                'meta': meta,
                'desc': desc,
                'waypoints': waypoints,
                'source': '小红书',
                'likes': likes,
                'updated': date.today().isoformat(),
            }
            all_routes.append(route)
            print(f"  ✅ {name}: {' → '.join(waypoints)}")

        time.sleep(3)  # 礼貌间隔

    return all_routes


def update_routes_json(new_routes, filepath='routes.json'):
    """合并新路线到 routes.json"""
    # 读取现有路线
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = []

    existing_names = {r['name'] for r in existing}
    today = date.today().isoformat()

    # 更新现有路线的日期
    for r in existing:
        r['updated'] = today

    # 添加新路线
    added = 0
    for route in new_routes:
        if route['name'] not in existing_names:
            existing.append(route)
            existing_names.add(route['name'])
            added += 1

    # 按点赞数排序
    existing.sort(key=lambda r: r.get('likes', 0), reverse=True)

    # 最多保留 20 条
    existing = existing[:20]

    # 写回文件
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    return len(existing), added


if __name__ == '__main__':
    print(f"{'='*50}")
    print(f"🏔️ 杭州徒步路线自动更新 - {date.today().isoformat()}")
    print(f"{'='*50}")

    new_routes = search_routes()
    print(f"\n📊 共提取到 {len(new_routes)} 条新路线")

    total, added = update_routes_json(new_routes)
    print(f"📝 routes.json: 共 {total} 条路线，新增 {added} 条")
    print(f"\n✅ 更新完成!")
