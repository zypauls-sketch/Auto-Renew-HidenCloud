#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, time, random, requests
from playwright.sync_api import sync_playwright

# --- 环境变量 ---
COOKIE_VALUE = os.environ.get('COOKIE_VALUE') or ""    # remember_web cookie 值，必填
EMAIL        = os.environ.get('EMAIL') or ""           # 登录邮箱,可选，作为备用,TG通知需要填写
PASSWORD     = os.environ.get('PASSWORD') or ""        # 登录密码,可选，作为备用
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ""    # Telegram Bot Token,可选
TG_CHAT_ID   = os.environ.get('TG_CHAT_ID') or ""      # Telegram Chat ID,可选

BASE_URL = "https://dash.hidencloud.com"
LOGIN_URL = f"{BASE_URL}/auth/login"

# --- 代理配置（由工作流 shell 脚本写入 $GITHUB_ENV）---
IS_PROXY      = os.environ.get('IS_PROXY', 'false').lower() == 'true'
PROXY_SERVER  = os.environ.get('PROXY_SERVER') or "socks5://127.0.0.1:1080"
REQUESTS_PROXIES = {"http": PROXY_SERVER, "https": PROXY_SERVER} if IS_PROXY else None

# 日志
def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
"""

def get_current_ip(proxy_server=None):
    """获取当前出口IP"""
    proxies = {"http": proxy_server, "https": proxy_server} if (proxy_server and IS_PROXY) else None
    try:
        resp = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
        if resp.status_code == 200:
            return resp.text.strip()
        return "获取失败"
    except Exception as e:
        log(f"❌ 获取出口IP失败: {e}")
        return "获取失败"

def send_telegram_notification(status, old_due, new_due):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False
    
    # 获取运行时间（转换为 UTC+8）
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + '****' if len(EMAIL) >= 2 else '****'

    text = (
        f"🎉 <b>HidenCloud 续期通知</b>\n\n"
        f"状态: <b>{status}</b>\n"
        f"👤 账号: {masked_email}\n"
        f"📅 续期前到期：{old_due}\n"
        f"📅 续期后到期：{new_due}\n"
        f"🕒 执行时间：{now}"
    )
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10, proxies=REQUESTS_PROXIES)
        if resp.status_code == 200:
            log("✅ Telegram 通知发送成功")
            return True
        else:
            log(f"❌ Telegram 通知失败: {resp.text}")
            return False
    except Exception as e:
        log(f"❌ Telegram 通知异常: {e}")
        return False

def handle_cloudflare(page):
    """处理 Cloudflare Turnstile / Managed Challenge 验证"""
    iframe_selector = 'iframe[src*="challenges.cloudflare.com"]'
    if page.locator(iframe_selector).count() == 0:
        return True

    log("⚠️ 检测到 Cloudflare 验证...")
    start_time = time.time()
    while time.time() - start_time < 60:
        if page.locator(iframe_selector).count() == 0:
            log("✅ Cloudflare 验证通过！")
            return True
        try:
            frame = page.frame_locator(iframe_selector).first
            checkbox = frame.locator('input[type="checkbox"], div#challenge-stage, .ctp-checkbox-label')
            if checkbox.count() > 0 and checkbox.first.is_visible():
                log("🖱️ 点击 Cloudflare 验证复选框...")
                time.sleep(random.uniform(0.5, 1.5))
                checkbox.first.click(force=True)
                log("⏳ 已点击，等待验证生效...")
                time.sleep(4)
            else:
                time.sleep(1)
        except Exception:
            time.sleep(1)

    log("❌ Cloudflare 验证超时。")
    return False

def login(page):
    # 1. Cookie 登录尝试
    if COOKIE_VALUE:
        log("📇 尝试 Cookie 登录...")
        try:
            page.context.add_cookies([{
                'name': 'remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d',
                'value': COOKIE_VALUE,
                'domain': 'dash.hidencloud.com',
                'path': '/',
                'expires': int(time.time()) + 3600 * 24 * 365,
                'httpOnly': True,
                'secure': True,
                'sameSite': 'Lax'
            }])
            page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
            handle_cloudflare(page)
            page_title = page.title()
            log(f"📝 当前Title: {page_title}")
            if "auth/login" not in page.url:
                log("✅ Cookie 登录成功！当前已到达 dashboard 页面")
                return True
            log("❌ Cookie 失效，尝试切换账号密码登录...")
        except Exception as e:
            log(f"⚠️ Cookie 登录过程出错: {e}")

    # 2. 账号密码登录
    if not EMAIL or not PASSWORD:
        log("❌ 未配置 EMAIL 或 PASSWORD，无法使用账号密码登录")
        return False

    log("💣 尝试账号密码登录...")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        page.fill('input[name="email"]', EMAIL)
        page.fill('input[name="password"]', PASSWORD)
        time.sleep(0.5)
        handle_cloudflare(page)
        page.click('button[type="submit"]')
        time.sleep(3)
        handle_cloudflare(page)
        page.wait_for_url(f"{BASE_URL}/*", timeout=30000)
        page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        page_title = page.title()
        log(f"📝 当前Title: {page_title}")
        if "auth/login" in page.url:
            log("❌ 登录失败。")
            page.screenshot(path="login_fail.png")
            return False
        log("✅ 账号密码登录成功！当前已到达 dashboard 页面")
        return True
    except Exception as e:
        log(f"❌ 登录异常: {e}")
        page.screenshot(path="login_fail.png")
        return False

def get_server_id(page):
    try:
        handle_cloudflare(page)
        time.sleep(3)
        html = page.content()
        log(f"📝 页面长度: {len(html)}, URL: {page.url}")

        # 方案1: 从 href 链接中提取 /service/数字/manage
        matches = re.findall(r'/service/(\d+)/manage', html)
        if matches:
            server_id = matches[0]
            log(f"✅ 从链接中获取到 Server ID: {server_id}")
            return server_id

        # 方案2: 从文本中提取 Free Server #数字
        matches = re.findall(r'#(\d{4,})', html)
        if matches:
            server_id = matches[0]
            log(f"✅ 从文本 #号中获取到 Server ID: {server_id}")
            return server_id

        log("❌ 未能获取到任何有效的 Server ID")
        page.screenshot(path="server_id_error.png")
        return None
    except Exception as e:
        log(f"❌ 获取 Server ID 失败: {e}")
        page.screenshot(path="server_id_error.png")
        return None

def get_due_date(page):
    try:
        if SERVICE_URL not in page.url:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        time.sleep(2)
        body_text = page.locator("body").inner_text()
        patterns = [
            r"Due date\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date\s*\n\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date.*?(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Expires on\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})"
        ]
        for pattern in patterns:
            match = re.search(pattern, body_text, re.IGNORECASE | re.DOTALL)
            if match:
                due_date = match.group(1).strip()
                log(f"📅 获取到 Due Date: {due_date}")
                return due_date
    except Exception as e:
        log(f"❌ 获取 Due Date 失败: {e}")
    return "未知"

def renew_service(page):
    try:
        log("➡ 进入续期流程...")
        if page.url != SERVICE_URL:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)

        # 1. 检查页面上是否已有未到期提示
        body_text = page.locator("body").inner_text()
        if "renewal restricted" in body_text.lower() or "can only renew" in body_text.lower():
            log("⚠️ 未到续期时间，当前无法续期。")
            page.screenshot(path="renew_not_allowed.png")
            return "NOT_TIME"

        log("🖱️ 查找 'Renew' 按钮...")
        renew_btn = page.locator('button[data-modal-target*="renewService"], button:has-text("Renew")').first
        try:
            renew_btn.wait_for(state="visible", timeout=15000)
        except Exception:
            log("❌ 未找到 Renew 按钮，可能页面未加载完成或无需续费")
            page.screenshot(path="renew_btn_not_found.png")
            return False

        # 2. 点击 Renew 按钮唤起弹窗
        log("🖱️ 点击 'Renew' 按钮...")
        renew_btn.scroll_into_view_if_needed()
        try:
            renew_btn.click(timeout=5000)
        except Exception:
            log("⚠️ 常规点击被阻挡，尝试强制点击...")
            renew_btn.click(force=True)

        time.sleep(2)
        # 弹窗打开后可能立即出现 Cloudflare 验证或遮罩层，必须在此处处理
        handle_cloudflare(page)

        # 3. 再次检查是否弹出“未到续期时间”的 Toast / 提示
        time.sleep(1)
        curr_text = page.locator("body").inner_text()
        if "renewal restricted" in curr_text.lower() or "can only renew" in curr_text.lower():
            log("⚠️ 收到提示：未到续期时间，无法续期。")
            page.screenshot(path="renew_not_allowed.png")
            return "NOT_TIME"

        # 4. 查找并等待弹窗内部的确认/创建发票按钮（支持多种可能的选择器）
        candidate_selectors = [
            'button:has-text("Create Invoice")',
            'button:has-text("Create invoice")',
            'input[value*="Invoice" i]',
            'div[id*="renewService"] button[type="submit"]',
            'div[role="dialog"] button[type="submit"]',
            'button:has-text("Renew"):visible',
            'a:has-text("Create Invoice")',
        ]

        target_btn = None
        log("🖲️ 等待续费确认按钮出现...")
        start_find = time.time()
        while time.time() - start_find < 15:
            handle_cloudflare(page)
            for sel in candidate_selectors:
                loc = page.locator(sel)
                # 排除最开始主界面的 Renew 触发按钮
                for idx in range(loc.count()):
                    btn = loc.nth(idx)
                    if btn.is_visible():
                        # 避开外部的 data-modal-toggle 按钮
                        if not btn.get_attribute("data-modal-toggle"):
                            target_btn = btn
                            log(f"✅ 找到确认按钮: {sel}")
                            break
                if target_btn:
                    break
            if target_btn:
                break
            time.sleep(1)

        if not target_btn:
            log("❌ 错误：弹窗中未找到确认/生成发票按钮。")
            page.screenshot(path="renew_modal_failed.png")
            return False

        # 5. 点击确认/生成发票按钮
        log("🖱️ 点击确认按钮创建发票...")
        try:
            target_btn.click(timeout=5000)
        except Exception:
            target_btn.click(force=True)

        # 6. 等待发票页面跳转
        new_invoice_url = None
        start_wait = time.time()
        while time.time() - start_wait < 90:
            if "/payment/invoice/" in page.url or "/invoice/" in page.url:
                new_invoice_url = page.url
                log(f"🎉 页面已跳转至发票页: {new_invoice_url}")
                break
            handle_cloudflare(page)
            time.sleep(1)

        if not new_invoice_url:
            log("❌ 未能进入发票页面，跳转超时。")
            page.screenshot(path="renew_stuck_invoice.png")
            return False

        if page.url != new_invoice_url:
            page.goto(new_invoice_url, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)

        # 7. 查找并点击 'Pay' 按钮
        log("🔎 查找 'Pay' 按钮...")
        pay_btn = page.locator('a:has-text("Pay"):visible, button:has-text("Pay"):visible, input[value*="Pay" i]:visible').first
        pay_btn.wait_for(state="visible", timeout=30000)
        pay_btn.scroll_into_view_if_needed()
        try:
            pay_btn.click(timeout=5000)
        except Exception:
            pay_btn.click(force=True)
        log("✅ 'Pay' 按钮已点击。")

        # 8. 等待支付确认完成，返回服务页确认新到期时间
        time.sleep(6)
        page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        return True

    except Exception as e:
        log(f"❌ 续费过程异常: {e}")
        page.screenshot(path="renew_error.png")
        return False

def main():
    # 检查必要环境变量
    if not COOKIE_VALUE and not (EMAIL and PASSWORD):
        log("❌ 缺少登录凭证，请至少配置 COOKIE_VALUE 或 EMAIL+PASSWORD")
        sys.exit(1)

    global SERVICE_URL

    with sync_playwright() as p:
        browser = None
        try:
            if IS_PROXY:
                log(f"⚙️ 代理已启用: {PROXY_SERVER}")
            else:
                log("🌐 直连模式（未使用代理）")
            
            # 获取当前出口 IP
            current_ip = get_current_ip(PROXY_SERVER)
            log(f"🎯 当前出口IP: {current_ip}")

            log("🚀 启动浏览器...")
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                args=[
                    '--no-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-infobars',
                    '--disable-dev-shm-usage'
                ]
            )
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
                proxy={"server": PROXY_SERVER} if IS_PROXY else None
            )
            page = context.new_page()
            page.add_init_script(STEALTH_JS)

            if not login(page):
                sys.exit(1)

            # 登录成功后，自动获取 Server ID
            server_id = get_server_id(page)
            if not server_id:
                log("❌ 无法获取 Server ID，退出。")
                sys.exit(1)
            SERVICE_URL = f"{BASE_URL}/service/{server_id}/manage"

            # 获取旧到期时间
            old_due = get_due_date(page)
            log(f"📆 续费前到期时间：{old_due}")

            # 执行续费
            renew_result = renew_service(page)

            new_due = old_due
            if renew_result == "NOT_TIME":
                log("⏳ 未到续期时间，目前无需续期")
                status = "⏳ 未到续期时间"
            elif renew_result is False:
                log("❌ 续费失败，脚本退出。")
                status = "❌ 续期失败"
            else:  # renew_result is True
                new_due = get_due_date(page)
                log(f"📆 续费后到期时间：{new_due}")
                status = "✅ 续期成功"

            # 发送 Telegram 通知
            send_telegram_notification(status, old_due, new_due)

            if renew_result == "NOT_TIME":
                sys.exit(0)
            elif renew_result is False:
                sys.exit(1)
            else:
                sys.exit(0)
        except Exception as e:
            log(f"❌ 脚本执行出错: {e}")
            sys.exit(1)
        finally:
            if browser:
                browser.close()

if __name__ == "__main__":
    main()
