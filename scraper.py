"""
XHS Cloud Scraper — GitHub Actions 版
采集小红书商品数据，保存到 Supabase
"""
import asyncio, json, re, os, http.client, urllib.parse
from typing import Optional

SUPABASE_URL = os.environ['SUPABASE_URL'].rstrip('/')
SUPABASE_KEY = os.environ['SUPABASE_KEY']


# ─── Supabase REST ──────────────────────────────────────────────────────────

def supa(method: str, table: str, body=None, query: str = '', headers_extra: dict = None):
    parsed = urllib.parse.urlparse(SUPABASE_URL)
    conn = http.client.HTTPSConnection(parsed.netloc, timeout=30)
    hdrs = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation',
    }
    if headers_extra:
        hdrs.update(headers_extra)
    path = f'/rest/v1/{table}' + (f'?{query}' if query else '')
    data = json.dumps(body).encode() if body is not None else None
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read().decode()
    try:
        return json.loads(raw), resp.status
    except Exception:
        return raw, resp.status


# ─── 解析 ───────────────────────────────────────────────────────────────────

def extract_url(raw: str) -> str:
    raw = raw.strip()
    m = re.search(r'https?://[^\s<>"）】」…，。；：]+', raw)
    return m.group(0).rstrip('.,;:…。，；：)"') if m else raw


def resolve_short_url(url: str) -> str:
    if 'xhslink.com' not in url:
        return url
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
        print(f'  短链解析失败: {e}')
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

        desc   = td.get('descriptionH5', {})
        price  = td.get('priceH5', {})
        deal   = price.get('dealPrice', {}) or {}
        seller = td.get('sellerH5', {})
        orig_p = float(price.get('highlightPrice') or 0)
        curr_p = float(deal.get('price') or orig_p)

        return {
            'product_id':      desc.get('skuId', ''),
            'name':            desc.get('name', '').strip(),
            'price':           curr_p,
            'original_price':  orig_p,
            'sold':            parse_num(price.get('itemAnalysisDataText', '')),
            'shop_name':       seller.get('name', ''),
            'shop_score':      str(seller.get('sellerScore', '')),
            'shop_fans':       str(seller.get('fansAmount', '')),
            'shop_total_sold': str(seller.get('salesVolume', '')),
        }
    except Exception as e:
        print(f'  API解析异常: {e}')
        return None


async def scrape_url(url: str) -> Optional[dict]:
    from playwright.async_api import async_playwright
    clean    = extract_url(url)
    resolved = resolve_short_url(clean)
    print(f'  采集: {resolved[:80]}')

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage',
                  '--disable-blink-features=AutomationControlled']
        )
        ctx  = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
        )
        page = await ctx.new_page()
        result = {}

        async def on_response(resp):
            if 'mall.xiaohongshu.com/api/store/jpd/edith/detail' in resp.url:
                try:
                    parsed = parse_goods_api(await resp.json())
                    if parsed:
                        result.update(parsed)
                        result['url'] = resolved
                except Exception:
                    pass

        page.on('response', on_response)
        try:
            await page.goto(resolved, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(4)
            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(1)
        except Exception as e:
            print(f'  页面加载异常: {e}')
        finally:
            await ctx.close()
            await browser.close()

    if result.get('name'):
        result['url'] = resolved
        return result
    return None


# ─── 保存到 Supabase ────────────────────────────────────────────────────────

def save_product(data: dict):
    pid = data.get('product_id', '')
    if not pid:
        return

    # upsert product（保留 created_at，不覆盖）
    supa('POST', 'products', data,
         headers_extra={'Prefer': 'resolution=merge-duplicates,return=minimal'})

    # 插入历史（5分钟内已有则跳过）
    existing, _ = supa('GET', 'price_history',
                        query=f"product_id=eq.{urllib.parse.quote(pid)}"
                              f"&recorded_at=gte.{_minutes_ago(5)}"
                              f"&limit=1&select=id")
    if isinstance(existing, list) and len(existing) == 0:
        supa('POST', 'price_history', {
            'product_id': pid,
            'price': data.get('price', 0),
            'sold':  data.get('sold', 0),
        })
        print(f'  历史记录已保存')


def _minutes_ago(n: int) -> str:
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) - timedelta(minutes=n)).strftime('%Y-%m-%dT%H:%M:%SZ')


# ─── 主流程 ─────────────────────────────────────────────────────────────────

async def main():
    print('=== XHS Cloud Scraper 启动 ===\n')

    # 1. 处理采集队列中的 pending 任务
    queue, _ = supa('GET', 'scrape_queue', query='status=eq.pending&order=created_at.asc')
    if isinstance(queue, list) and queue:
        print(f'队列中有 {len(queue)} 个待采集任务')
        for item in queue:
            qid = item['id']
            url = item['url']
            print(f'\n[队列 #{qid}] {url[:60]}')

            # 标记为处理中
            supa('PATCH', f'scrape_queue?id=eq.{qid}', {'status': 'processing'})

            try:
                result = await scrape_url(url)
                if result:
                    save_product(result)
                    supa('PATCH', f'scrape_queue?id=eq.{qid}',
                         {'status': 'done', 'processed_at': _minutes_ago(0)})
                    print(f'  完成: {result["name"][:40]} | 价格:{result["price"]} | 销量:{result["sold"]}')
                else:
                    supa('PATCH', f'scrape_queue?id=eq.{qid}',
                         {'status': 'error', 'error': '采集失败', 'processed_at': _minutes_ago(0)})
                    print(f'  失败: 未采集到数据')
            except Exception as e:
                supa('PATCH', f'scrape_queue?id=eq.{qid}',
                     {'status': 'error', 'error': str(e)[:200], 'processed_at': _minutes_ago(0)})
                print(f'  异常: {e}')
    else:
        print('队列为空，跳过')

    # 2. 每日刷新：重新采集所有商品
    products, _ = supa('GET', 'products', query='select=product_id,url,name&order=created_at.asc')
    if isinstance(products, list) and products:
        print(f'\n定时刷新 {len(products)} 个商品...')
        for p in products:
            pid  = p['product_id']
            url  = p['url']
            name = p.get('name', '')[:30]
            if not url:
                continue
            print(f'\n[刷新] {name or pid}')
            try:
                result = await scrape_url(url)
                if result:
                    save_product(result)
                    print(f'  完成: 价格:{result["price"]} | 销量:{result["sold"]}')
                else:
                    print(f'  失败')
            except Exception as e:
                print(f'  异常: {e}')

    print('\n=== 全部完成 ===')


if __name__ == '__main__':
    asyncio.run(main())
