#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, time, random, requests
try:
    from patchright.sync_api import sync_playwright
except ImportError:
    from playwright.sync_api import sync_playwright

# --- 环境变量 ---
COOKIE_VALUE = os.environ.get('COOKIE_VALUE') or ""    # remember_web cookie 值
EMAIL        = os.environ.get('EMAIL') or ""           # 登录邮箱
PASSWORD     = os.environ.get('PASSWORD') or ""        # 登录密码
TG_CHAT_ID   = os.environ.get('TG_CHAT_ID') or ""      # Telegram Chat ID
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ""    # Telegram Bot Token

BASE_URL = "https://dash.hidencloud.com"
LOGIN_URL = f"{BASE_URL}/auth/login"

IS_PROXY      = os.environ.get('IS_PROXY', 'false').lower() == 'true'
PROXY_SERVER  = os.environ.get('PROXY_SERVER') or "socks5://127.0.0.1:1080"
REQUESTS_PROXIES = {"http": PROXY_SERVER, "https": PROXY_SERVER} if IS_PROXY else None

def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.canRunAds = true;
window.isAdBlockActive = false;
window.adsbygoogle = window.adsbygoogle || [];
window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {};
window.chrome.loadTimes = window.chrome.loadTimes || function () { return {}; };
window.chrome.csi = window.chrome.csi || function () { return {}; };
if (!window.chrome.app) {
  window.chrome.app = { isInstalled: false,
    InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
    RunningState: { CANT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } };
}
try {
  const origQuery = window.navigator.permissions && window.navigator.permissions.query;
  if (origQuery) {
    window.navigator.permissions.query = (p) =>
      (p && p.name === 'notifications')
        ? Promise.resolve({ state: (window.Notification && Notification.permission) || 'prompt' })
        : origQuery(p);
  }
} catch (e) {}
"""

def dismiss_adblock_overlay(page):
    """安全隐藏 'Ad blocker detected' 等拦截弹窗与遮罩，彻底解除 pointer-events 拦截"""
    try:
        page.evaluate("""() => {
            const elements = Array.from(document.querySelectorAll('*')).filter(el => 
                (el.innerText && el.innerText.includes('Ad blocker detected')) ||
                (el.id && el.id.startsWith('x1'))
            );
            for (const el of elements) {
                let cur = el;
                while (cur && cur !== document.body) {
                    if (cur.id === 'app' || cur.tagName === 'MAIN' || cur.tagName === 'BODY' || cur.tagName === 'HTML' || cur.classList.contains('main-wrapper')) {
                        break;
                    }
                    const style = window.getComputedStyle(cur);
                    if (style.position === 'fixed' || style.position === 'absolute' || (cur.id && cur.id.startsWith('x1'))) {
                        cur.style.setProperty('display', 'none', 'important');
                        cur.style.setProperty('visibility', 'hidden', 'important');
                        cur.style.setProperty('pointer-events', 'none', 'important');
                        break;
                    }
                    cur = cur.parentElement;
                }
            }
            document.querySelectorAll('div[id^="x1"], div[class*="backdrop"], div[class*="overlay"]').forEach(el => {
                if (!el.querySelector('#renewService, form, [data-modal]')) {
                    el.style.setProperty('display', 'none', 'important');
                    el.style.setProperty('pointer-events', 'none', 'important');
                }
            });
            document.body.style.setProperty('overflow', 'auto', 'important');
            document.body.style.setProperty('pointer-events', 'auto', 'important');
            if (document.documentElement) {
                document.documentElement.style.setProperty('overflow', 'auto', 'important');
                document.documentElement.style.setProperty('pointer-events', 'auto', 'important');
            }
        }""")
    except Exception:
        pass

def get_current_ip(proxy_server=None):
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
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False

    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        masked_EMAIL = f"{name[:2]}****{name[-2:]}@{domain}" if len(name) > 4 else f"{name}@{domain}"
    else:
        masked_EMAIL = EMAIL[:2] + '****'

    text = (
        f"🎉 HidenCloud 续期通知\n\n"
        f"{status}\n"
        f"👤 账号: {masked_EMAIL}\n"
        f"📅 续期前到期：{old_due}\n"
        f"📅 续期后到期：{new_due}\n"
        f"🕒 续期时间：{now}"
    )
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}
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

TURNSTILE_IFRAME_SEL = 'iframe[src*="challenges.cloudflare.com"], iframe[title*="Cloudflare"]'
TURNSTILE_FRAME_URL_MARKER = 'challenges.cloudflare.com'

TURNSTILE_STATE_JS = """
() => {
    try {
        let total = 0, solved = 0;
        document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]').forEach(n => {
            total += 1;
            if (n.value && n.value.length > 20) solved += 1;
        });
        return { total: total, solved: solved };
    } catch (e) { return { total: 0, solved: 0 }; }
}
"""

_CDP_SESSIONS = {}

def get_cdp_session(page):
    session = _CDP_SESSIONS.get(page)
    if session is None:
        try:
            session = page.context.new_cdp_session(page)
        except Exception as e:
            log(f"⚠️ 创建 CDP 会话失败: {e}")
            return None
        _CDP_SESSIONS[page] = session
    return session

def reset_cdp_session(page):
    session = _CDP_SESSIONS.pop(page, None)
    try:
        if session is not None:
            session.detach()
    except Exception:
        pass

def cdp_click_at(page, x, y):
    session = get_cdp_session(page)
    if not session:
        return False
    try:
        sx = x - random.uniform(50, 110)
        sy = y - random.uniform(35, 75)
        steps = random.randint(8, 14)
        for i in range(1, steps + 1):
            ix = sx + (x - sx) * i / steps + random.uniform(-1.5, 1.5)
            iy = sy + (y - sy) * i / steps + random.uniform(-1.5, 1.5)
            session.send('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': ix, 'y': iy})
            time.sleep(random.uniform(0.01, 0.035))
        time.sleep(random.uniform(0.1, 0.25))
        session.send('Input.dispatchMouseEvent', {
            'type': 'mousePressed', 'x': x, 'y': y,
            'button': 'left', 'buttons': 1, 'clickCount': 1
        })
        time.sleep(random.uniform(0.05, 0.12))
        session.send('Input.dispatchMouseEvent', {
            'type': 'mouseReleased', 'x': x, 'y': y,
            'button': 'left', 'clickCount': 1
        })
        return True
    except Exception as e:
        log(f"⚠️ CDP 底层点击失败: {e}")
        reset_cdp_session(page)
        return False

def turnstile_state(page):
    try:
        st = page.evaluate(TURNSTILE_STATE_JS)
        if isinstance(st, dict):
            return {"total": int(st.get("total", 0)), "solved": int(st.get("solved", 0))}
    except Exception:
        pass
    return {"total": 0, "solved": 0}

def _overlaps(box, boxes, dx=25, dy=25, dw=60):
    for b in boxes:
        if (abs(b['x'] - box['x']) < dx and abs(b['y'] - box['y']) < dy
                and abs(b['width'] - box['width']) < dw):
            return True
    return False

def challenge_frames(page):
    targets = []
    seen = []
    try:
        for f in page.frames:
            if TURNSTILE_FRAME_URL_MARKER not in (f.url or ''):
                continue
            try:
                fe = f.frame_element()
                if fe.is_visible():
                    box = fe.bounding_box()
                    if box and box.get('width', 0) > 10 and box.get('height', 0) > 10:
                        seen.append(box)
                        targets.append((fe, box))
            except Exception:
                continue
    except Exception:
        pass

    try:
        for el in page.locator(TURNSTILE_IFRAME_SEL).all():
            try:
                if not el.is_visible():
                    continue
                box = el.bounding_box()
                if box and box.get('width', 0) > 10 and box.get('height', 0) > 10 \
                        and not _overlaps(box, seen):
                    seen.append(box)
                    targets.append((el, box))
            except Exception:
                continue
    except Exception:
        pass

    return targets

def challenge_containers(page):
    targets = []
    try:
        for el in page.locator(
                'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]').all():
            try:
                if el.evaluate("n => !!(n.value && n.value.length > 20)"):
                    continue
                box = el.evaluate("""n => {
                    let p = n.parentElement;
                    for (let i = 0; i < 4 && p; i++) {
                        const r = p.getBoundingClientRect();
                        if (r.width > 40 && r.height > 20)
                            return {x: r.x, y: r.y, width: r.width, height: r.height};
                        p = p.parentElement;
                    }
                    return null;
                }""")
                if box and not _overlaps(box, [b for _, b in targets]):
                    targets.append((None, box))
            except Exception:
                continue
    except Exception:
        pass
    return targets

def challenge_boxes(page):
    frames = challenge_frames(page)
    seen = [b for _, b in frames]
    targets = list(frames)
    for el, box in challenge_containers(page):
        if not _overlaps(box, seen):
            targets.append((el, box))
    return targets

def page_ready(p):
    try:
        t = (p.title() or "").lower()
        blocked = ("just a moment", "attention required", "checking your browser",
                   "请稍候", "security verification", "请验证")
        return bool(t) and not any(k in t for k in blocked)
    except Exception:
        return False

def solve_turnstile(page, timeout=120, success_check=None,
                    require_positive=False, appear_grace=5, reload_after=None,
                    shot_on_timeout="turnstile_timeout.png"):

    log(f"🛡️ 开始处理 Turnstile...")
    start = time.time()
    baseline = turnstile_state(page)
    had_iframe = False
    iframe_gone_since = None
    container_only_since = None
    click_count = 0
    reload_done = 0

    while time.time() - start < timeout:
        if success_check is not None:
            try:
                if success_check(page):
                    log("✅ Turnstile 验证通过！")
                    return True
            except Exception:
                pass

        st = turnstile_state(page)
        if st["total"] > 0 and st["solved"] >= st["total"] and (
                st["total"] > baseline["total"] or st["solved"] > baseline["solved"]):
            log(f"✅ Turnstile 验证通过（token 已生成 {st['solved']}/{st['total']}）！")
            return True

        frames = challenge_frames(page)
        seen = [b for _, b in frames]
        targets = list(frames) + [(el, b) for el, b in challenge_containers(page)
                                  if not _overlaps(b, seen)]

        if frames:
            had_iframe = True
            iframe_gone_since = None
            container_only_since = None
        elif targets:
            had_iframe = True
            iframe_gone_since = None
            if container_only_since is None:
                container_only_since = time.time()
            elif time.time() - container_only_since >= 12:
                log("✅ Turnstile 验证通过（挑战已结束）！")
                return True
        else:
            container_only_since = None
            if had_iframe:
                if iframe_gone_since is None:
                    iframe_gone_since = time.time()
                elif time.time() - iframe_gone_since >= 8:
                    log("✅ Turnstile 验证通过（挑战框已消失）！")
                    return True
            elif (not require_positive and success_check is None
                    and time.time() - start >= appear_grace):
                log("ℹ️ 页面未出现 Turnstile，无需处理")
                return True
            time.sleep(1)
            continue

        for el, box in targets:
            clicked = False
            try:
                off_x = min(30, box['width'] / 2)
                pos_y = box['height'] / 2
                if el is not None:
                    try:
                        el.scroll_into_view_if_needed(timeout=3000)
                    except Exception:
                        pass
                    try:
                        el.click(position={'x': off_x, 'y': pos_y}, timeout=5000)
                        clicked = True
                        log(f"🖱️ 点击 Turnstile 验证 ({box['x'] + off_x:.0f}, {box['y'] + pos_y:.0f}) ...")
                    except Exception:
                        pass
                if not clicked:
                    cx = box['x'] + off_x + random.uniform(-2, 2)
                    cy = box['y'] + pos_y + random.uniform(-2, 2)
                    log(f"🖱️ CDP 底层点击 Turnstile ({cx:.0f}, {cy:.0f}) ...")
                    clicked = cdp_click_at(page, cx, cy)
            except Exception as e:
                log(f"⚠️ 点击挑战框出错: {e}")
            click_count += 1
            time.sleep(random.uniform(4.0, 6.0))

        if reload_after and click_count >= reload_after and reload_done < 2:
            reload_done += 1
            log(f"🔄 累计点击 {click_count} 次未通过，刷新页面重试（第 {reload_done}/2 次）...")
            click_count = 0
            had_iframe = False
            iframe_gone_since = None
            container_only_since = None
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log(f"⚠️ 刷新失败: {e}")
            time.sleep(random.uniform(3.0, 5.0))

    log(f"❌ Turnstile 处理超时（{timeout}s）")
    try:
        cf = [f.url[:100] for f in page.frames if TURNSTILE_FRAME_URL_MARKER in (f.url or '')]
        st = turnstile_state(page)
        log(f"🔍 超时现场: cf_frames={len(cf)} token={st} title={page.title()!r}")
        page.screenshot(path=shot_on_timeout)
    except Exception:
        pass
    return False

def login(page):
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
            solve_turnstile(page, timeout=90, success_check=page_ready, reload_after=8)
            page_title = page.title()
            log(f"📝 当前Title: {page_title}")
            if "auth/login" not in page.url:
                log(f"✅ Cookie 登录成功！当前已到达dashboard页面")
                return True
            log("⚠️ Cookie 失效，切换到账号密码登录...")
        except Exception:
            log(f"⚠️ Cookie 登录出现异常，回退账号密码登录...")

    if not EMAIL or not PASSWORD:
        log("❌ 未配置 EMAIL/PASSWORD，无法进行账号密码登录")
        return False
    log("💣 尝试账号密码登录...")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

        def login_form_visible(p):
            try:
                return p.locator('input[type="password"]').first.is_visible()
            except Exception:
                return False

        log("🛡️ 处理登录页第一道 Turnstile验证...")
        if not solve_turnstile(page, timeout=180, success_check=login_form_visible,
                               reload_after=8, shot_on_timeout="login_turnstile1_fail.png"):
            log("❌ 第一道 Turnstile 未通过，无法进入登录表单")
            page.screenshot(path="login_turnstile1_fail.png")
            return False

        email_sel = ('input[name="username"], input#username, input[name="email"], '
                     'input[type="email"], input[name="EMAIL"]')
        pwd_sel = ('input[name="password"], input#password, '
                   'input[name="PASSWORD"], input[type="password"]')
        email_input = page.locator(email_sel).first
        pwd_input = page.locator(pwd_sel).first
        email_input.wait_for(state="visible", timeout=60000)
        log("⌨️ 输入账号...")
        email_input.click()
        email_input.fill(EMAIL)
        time.sleep(random.uniform(0.8, 1.5))
        log("⌨️ 输入密码...")
        pwd_input.click()
        pwd_input.fill(PASSWORD)

        log("⏳ 输入完成，等待turnstile加载...")
        time.sleep(8)

        log("🛡️ 处理第二道 Turnstile...")
        if not solve_turnstile(page, timeout=90, require_positive=True,
                               shot_on_timeout="login_turnstile2_fail.png"):
            log("⚠️ 第二道 Turnstile 未确认通过，仍尝试点击登录...")

        submit_btn = page.locator('button[type="submit"], button:has-text("Login"), '
                                  'button:has-text("Sign in"), button:has-text("登录")').first
        log("🖱️ 点击登录按钮...")
        try:
            submit_btn.click(timeout=15000)
        except Exception as e:
            log(f"⚠️ 点击登录按钮失败: {e}")
            page.screenshot(path="login_submit_fail.png")
            return False

        solve_turnstile(page, timeout=45, success_check=lambda p: "auth/login" not in p.url,
                        shot_on_timeout="login_turnstile3_fail.png")
        try:
            page.wait_for_url(lambda u: "auth/login" not in u, timeout=30000)
        except Exception:
            pass

        page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
        solve_turnstile(page, timeout=60, success_check=page_ready, reload_after=8)
        page_title = page.title()
        log(f"📝 当前Title: {page_title}")
        if "auth/login" in page.url:
            log("❌ 登录失败，账号密码错误或被封禁")
            page.screenshot(path="login_fail.png")
            return False
        log(f"✅ 账号密码登录成功！当前已到达dashboard页面")
        return True
    except Exception as e:
        log(f"❌ 登录异常: {e}")
        page.screenshot(path="login_fail.png")
        return False

def get_server_id(page):
    try:
        solve_turnstile(page, timeout=60, success_check=page_ready, reload_after=8)
        dismiss_adblock_overlay(page)
        time.sleep(3)
        html = page.content()
        log(f"📝 页面长度: {len(html)}, URL: {page.url}")

        matches = re.findall(r'/service/(\d+)/manage', html)
        if matches:
            server_id = matches[0]
            log(f"✅ 从链接中获取到 Server ID: {server_id}")
            return server_id

        matches = re.findall(r'#(\d{4,})', html)
        if matches:
            server_id = matches[0]
            log(f"✅ 从文本 #号中获取到 Server ID: {server_id}")
            return server_id

        fallback_id = os.environ.get('SERVER_ID', '230706')
        log(f"⚠️ 正则未匹配到，使用保底 Server ID: {fallback_id}")
        return fallback_id
    except Exception as e:
        log(f"❌ 获取 Server ID 失败: {e}")
        return "230706"

def get_due_date(page):
    try:
        if SERVICE_URL not in page.url:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        solve_turnstile(page, timeout=60, success_check=page_ready, reload_after=8)
        dismiss_adblock_overlay(page)
        body_text = page.locator("body").inner_text()
        patterns = [
            r"Due date\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date\s*\n\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date.*?(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
        ]
        for pattern in patterns:
            match = re.search(pattern, body_text, re.IGNORECASE | re.DOTALL)
            if match:
                due_date = match.group(1).strip()
                log(f"📅 获取到Due Date: {due_date}")
                return due_date
    except Exception as e:
        log(f"❌ 获取Due Date失败: {e}")
    return "未知"

def check_and_pay_existing_invoice(page):
    """检查是否已经存在待支付的续费发票，若存在直接支付，绕过续费弹窗与人机验证"""
    try:
        invoices_url = f"{BASE_URL}/invoices"
        log(f"🔎 检查是否有现成待支付发票: {invoices_url}...")
        page.goto(invoices_url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)
        dismiss_adblock_overlay(page)

        unpaid_rows = page.locator('tr:has-text("Pending"), tr:has-text("Unpaid"), div:has-text("Pending"):has-text("€0.00"), div:has-text("Unpaid"):has-text("€0.00")')
        count = unpaid_rows.count()
        if count > 0:
            log(f"🎉 发现 {count} 笔待支付账单，准备直接支付...")
            first_row = unpaid_rows.first
            view_btn = first_row.locator('a:has-text("View"), a:has-text("Pay"), a[href*="/invoice/"]').first
            if view_btn.count() > 0:
                view_btn.click()
            else:
                first_row.click()
            time.sleep(3)
            dismiss_adblock_overlay(page)

            pay_btn = page.locator('a:has-text("Pay"):visible, button:has-text("Pay"):visible, input[value*="Pay" i]:visible').first
            if pay_btn.count() > 0:
                log("🖱️ 点击账单支付按钮...")
                try:
                    pay_btn.click(timeout=5000)
                except Exception:
                    try:
                        pay_btn.click(force=True, timeout=5000)
                    except Exception:
                        pay_btn.dispatch_event('click')
                time.sleep(5)
                log("✅ 现成账单已成功支付！")
                return True
    except Exception as e:
        log(f"ℹ️ 检查现成发票流程出错: {e}")
    return False

def renew_service(page):
    try:
        log("➡ 进入续期流程...")
        # 0. 优先检测是否已有现成账单待支付（直接支付免除弹窗麻烦）
        if check_and_pay_existing_invoice(page):
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
            time.sleep(2)
            dismiss_adblock_overlay(page)
            return True

        if page.url != SERVICE_URL:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        solve_turnstile(page, timeout=60, success_check=page_ready, reload_after=8)
        dismiss_adblock_overlay(page)

        log("🖱️ 准备点击 'Renew' 按钮...")
        renew_btn = page.locator('button:has-text("Renew")')
        create_btn = page.locator('button:has-text("Create Invoice")')

        modal_opened = False
        for i in range(6):
            try:
                dismiss_adblock_overlay(page)
                renew_btn.wait_for(state="visible", timeout=10000)
                renew_btn.scroll_into_view_if_needed()
                log(f"🖱️ 第 {i+1} 次尝试点击 'Renew'...")
                try:
                    renew_btn.click(timeout=3000)
                except Exception:
                    try:
                        renew_btn.click(force=True, timeout=3000)
                    except Exception:
                        renew_btn.dispatch_event('click')

                # 等待一小段时间，检测是否出现“未到续期时间”弹窗
                time.sleep(2)
                dismiss_adblock_overlay(page)
                page_text = page.locator("body").inner_text()
                if "Renewal Restricted" in page_text or "can only renew" in page_text.lower():
                    log("⚠️ 未到续期时间，无法续期。")
                    page.screenshot(path="renew_not_allowed.png")
                    return "NOT_TIME"   # 特殊状态

                log("🖲️ 等待弹窗出现...")
                try:
                    create_btn.wait_for(state="visible", timeout=5000)
                    modal_opened = True
                    log("✅ 弹窗已成功弹出！")
                    break
                except:
                    if challenge_boxes(page):
                        modal_opened = True
                        log("✅ 弹窗已弹出（先出现 Turnstile 验证）！")
                        break
                    log("⚠️ 弹窗未出现，可能是点击未响应，准备重试...")
                    time.sleep(2)
            except Exception as e:
                log(f"❌ 点击尝试出错: {e}")

        if not modal_opened:
            log("❌ 错误：尝试多次后，续费弹窗仍未出现。")
            page.screenshot(path="renew_modal_failed.png")
            return False

        log("🛡️ 处理弹窗内的 Turnstile...")
        if not solve_turnstile(page, timeout=90, require_positive=True,
                               shot_on_timeout="modal_turnstile_fail.png"):
            log("⚠️ 弹窗内 Turnstile 未确认通过，仍尝试点击 'Create Invoice'...")

        try:
            create_btn.wait_for(state="visible", timeout=30000)
        except Exception:
            pass

        create_clicked = False
        for i in range(3):
            try:
                dismiss_adblock_overlay(page)
                log(f"🖱️ 点击 'Create Invoice'（第 {i+1} 次）...")
                try:
                    create_btn.click(timeout=8000)
                except Exception:
                    try:
                        create_btn.click(force=True, timeout=5000)
                    except Exception:
                        create_btn.dispatch_event('click')
                create_clicked = True
                break
            except Exception as e:
                log(f"⚠️ 点击 'Create Invoice' 失败: {e}")
                solve_turnstile(page, timeout=30, require_positive=True)
        if not create_clicked:
            log("❌ 无法点击 'Create Invoice'。")
            page.screenshot(path="create_invoice_failed.png")
            return False

        new_invoice_url = None
        start_wait = time.time()
        while time.time() - start_wait < 90:
            if "/payment/invoice/" in page.url or "/invoice/" in page.url:
                new_invoice_url = page.url
                log(f"🎉 页面已跳转: {new_invoice_url}")
                break
            if page.locator('iframe[src*="challenges.cloudflare.com"]').count() > 0:
                log("⚠️ 遇到拦截，尝试处理...")
                solve_turnstile(page, timeout=45, reload_after=8)
            time.sleep(1)

        if not new_invoice_url:
            log("❌ 未能进入发票页面，超时。")
            page.screenshot(path="renew_stuck_invoice.png")
            return False

        if page.url != new_invoice_url:
            page.goto(new_invoice_url)
        solve_turnstile(page, timeout=60, success_check=page_ready, reload_after=8)
        dismiss_adblock_overlay(page)

        log("🔎 查找 'Pay' 按钮...")
        pay_btn = page.locator('a:has-text("Pay"):visible, button:has-text("Pay"):visible, input[value*="Pay" i]:visible').first
        pay_btn.wait_for(state="visible", timeout=30000)
        pay_btn.scroll_into_view_if_needed()
        try:
            pay_btn.click(timeout=5000)
        except Exception:
            try:
                pay_btn.click(force=True, timeout=5000)
            except Exception:
                pay_btn.dispatch_event('click')
        log("✅ 'Pay' 按钮已点击。")

        time.sleep(5)
        page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        solve_turnstile(page, timeout=60, success_check=page_ready, reload_after=8)
        return True

    except Exception as e:
        log(f"❌ 续费异常: {e}")
        page.screenshot(path="renew_error.png")
        return False

def main():
    log(f"🔍 凭证检测: COOKIE_VALUE={'已配置' if COOKIE_VALUE else '未配置'}, "
        f"EMAIL={'已配置' if EMAIL else '未配置'}, PASSWORD={'已配置' if PASSWORD else '未配置'}")
    if not COOKIE_VALUE and not (EMAIL and PASSWORD):
        log("❌ 缺少登录凭证")
        sys.exit(1)

    global SERVICE_URL

    with sync_playwright() as p:
        try:
            if IS_PROXY:
                log(f"⚙️ 代理已启用: {PROXY_SERVER}")
            else:
                log("🌐 直连模式（未使用代理）")

            current_ip = get_current_ip(PROXY_SERVER)
            log(f"🎯 当前出口IP: {current_ip}")

            log("🚀 启动浏览器...")
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                args=['--no-sandbox', '--disable-blink-features=AutomationControlled',
                      '--disable-infobars', '--window-size=1920,1080']
            )

            context = browser.new_context(
                no_viewport=True,
                proxy={"server": PROXY_SERVER} if IS_PROXY else None
            )
            page = context.new_page()
            page.add_init_script(STEALTH_JS)

            def mock_ad_response(route):
                req_url = route.request.url.lower()
                if "cloudflare.com" in req_url or "turnstile" in req_url or "hidencloud.com" in req_url:
                    route.continue_()
                    return
                fake_js = "window.canRunAds = true; window.isAdBlockActive = false; window.adsbygoogle = window.adsbygoogle || [];"
                try:
                    route.fulfill(status=200, content_type="application/javascript", body=fake_js)
                except Exception:
                    pass

            page.route("**/pagead2.googlesyndication.com/**", mock_ad_response)
            page.route("**/securepubads.g.doubleclick.net/**", mock_ad_response)
            page.route("**/adservice.google.**", mock_ad_response)
            page.route("**/*monetag*/**", mock_ad_response)
            page.route("**/*adsterra*/**", mock_ad_response)

            if not login(page):
                sys.exit(1)

            server_id = get_server_id(page)
            if not server_id:
                log("❌ 无法获取 Server ID，退出。")
                sys.exit(1)
            SERVICE_URL = f"{BASE_URL}/service/{server_id}/manage"

            old_due = get_due_date(page)
            log(f"📆 续费前到期时间：{old_due}")

            renew_result = renew_service(page)

            new_due = old_due
            if renew_result == "NOT_TIME":
                log("⏳ 未到续期时间，目前无法续期")
                status = "⏳ 未到续期时间"
            elif renew_result is False:
                log("❌ 续费失败，脚本退出。")
                status = "❌ 续期失败"
            else:
                new_due = get_due_date(page)
                log(f"📆 续费后到期时间：{new_due}")
                status = "✅ 续期成功"

            send_telegram_notification(status, old_due, new_due)

            if renew_result == "NOT_TIME":
                sys.exit(0)
            elif renew_result is False:
                sys.exit(1)
            else:
                sys.exit(0)
        except Exception as e:
            log(f"❌ 浏览器执行出错: {e}")
            sys.exit(1)
        finally:
            if 'browser' in locals() and browser:
                browser.close()

if __name__ == "__main__":
    main()
