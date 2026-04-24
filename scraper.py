"""
XHS Cloud Scraper - GitHub Actions 版
用 Playwright 采集小红书商品数据，结果输出到控制台（测试用）
后续可接入 Supabase 存储
"""
import asyncio
import json
import re
import sys
import os
from typing import Optional


def extract_url(raw: str) -> str:
    raw = raw.strip()
    m = re.search(r'https?://[^\s<>"）】」…，。；：]+', raw)
    if m:
        return m.group(0).rstrip('.,;:…。，；：)"')
    return raw


def resolve_short_url(url: str) -> str:
    if 'xhslink.com' not in url:
        return url
    import http.client, urllib.parse
    try:
        parsed = urllib.parse.urlparse(url)
        conn = http.client.HTTPSConnection(parsed.netloc, timeout=15)
        conn.request('GET', parsed.path, headers={
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15'
        })
        resp = conn.getresponse()
        if resp.status in (301, 302):
            loc = resp.getheader('Location', '')
            m = re.search(r'https?://www\.xiaohongshu\.com/(?:goods-detail|discovery/item|explore)/[a-f0-9]+', loc)
            if m:
                return m.group(0)
    except Exception as e:
        print(f"短链解析失败: {e}")
    return url


def parse_goods_api(data: dict) -> Optional[dict]:
    try:
        if not data.get('success'):
            return None
        td_list = data.get('data', {}).get('template_data', [])
        if not td_list:
            return None
        td = td_list[0]

        def parse_num(text):
            if not text:
                return 0
            m = re.search(r'([\d.]+)\s*万', str(text))
            if m:
                return int(float(m.group(1)) * 10000)
            m = re.search(r'[\d.]+', str(text))
            return int(float(m.group(0))) if m else 0

        desc  = td.get('descriptionH5', {})
        price = td.get('priceH5', {})
        deal  = price.get('dealPrice', {}) or {}
        seller = td.get('sellerH5', {})

        orig_p = float(price.get('highlightPrice') or 0)
        curr_p = float(deal.get('price') or orig_p)

        return {
            'product_id': desc.get('skuId', ''),
            'name':       desc.get('name', ''),
            'price':      curr_p,
            'original_price': orig_p,
            'sold':       parse_num(price.get('itemAnalysisDataText', '')),
            'shop_name':  seller.get('name', ''),
            'shop_score': seller.get('sellerScore', ''),
            'shop_fans':  seller.get('fansAmount', ''),
            'shop_total_sold': seller.get('salesVolume', ''),
        }
    except Exception as e:
        print(f"API解析异常: {e}")
        return None


async def scrape(url: str) -> Optional[dict]:
    from playwright.async_api import async_playwright

    clean = extract_url(url)
    resolved = resolve_short_url(clean)
    print(f"采集: {resolved[:80]}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage',
                  '--disable-blink-features=AutomationControlled']
        )
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
        )
        page = await ctx.new_page()

        result = {}

        async def on_response(resp):
            if 'mall.xiaohongshu.com/api/store/jpd/edith/detail' in resp.url:
                try:
                    data = await resp.json()
                    parsed = parse_goods_api(data)
                    if parsed:
                        result.update(parsed)
                        print(f"  [API拦截成功]")
                except Exception as e:
                    print(f"  [API解析失败] {e}")

        page.on('response', on_response)

        try:
            await page.goto(resolved, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(4)
            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except:
                pass
            await asyncio.sleep(2)
        except Exception as e:
            print(f"  页面加载异常: {e}")

        await ctx.close()
        await browser.close()

    return result if result.get('name') else None


async def main():
    # 从环境变量读取商品链接列表（逗号分隔），没有则用测试链接
    urls_env = os.environ.get('XHS_URLS', '')
    if urls_env:
        urls = [u.strip() for u in urls_env.split(',') if u.strip()]
    else:
        # 默认测试链接
        urls = [
            'https://xhslink.com/m/1OW8pnl8CG4'
        ]

    print(f"共 {len(urls)} 个商品待采集\n")
    results = []

    for url in urls:
        try:
            data = await scrape(url)
            if data:
                results.append(data)
                print(f"  商品名: {data.get('name','')[:40]}")
                print(f"  价格:   {data.get('price')}")
                print(f"  销量:   {data.get('sold')}")
                print(f"  店铺:   {data.get('shop_name','')[:30]}")
            else:
                print(f"  采集失败")
        except Exception as e:
            print(f"  异常: {e}")
        print()

    print(f"\n=== 完成: 成功 {len(results)}/{len(urls)} ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))

    # 如果配置了 Supabase，保存数据（预留，后续开启）
    supabase_url = os.environ.get('SUPABASE_URL', '')
    supabase_key = os.environ.get('SUPABASE_KEY', '')
    if supabase_url and supabase_key and results:
        save_to_supabase(results, supabase_url, supabase_key)


def save_to_supabase(results, url, key):
    import http.client, json, urllib.parse
    parsed = urllib.parse.urlparse(url)
    conn = http.client.HTTPSConnection(parsed.netloc, timeout=15)
    for item in results:
        body = json.dumps(item).encode()
        conn.request('POST', '/rest/v1/products',
            body=body,
            headers={
                'apikey': key,
                'Authorization': f'Bearer {key}',
                'Content-Type': 'application/json',
                'Prefer': 'resolution=merge-duplicates',
            }
        )
        resp = conn.getresponse()
        resp.read()
        print(f"Supabase保存: {resp.status} - {item.get('name','')[:30]}")


if __name__ == '__main__':
    asyncio.run(main())
