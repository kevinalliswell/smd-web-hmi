"""PR #84 UI 真机验收浏览器侧夹具（第 1—6 项）。

被测对象：main@0515727 工作树构建的 frontend/dist，由同提交后端（HOSTCOMM_MOCK=true）同源托管。
真实 Chromium（Playwright 1.62.0，与 tools/bench 锁定同版本），有头窗口 1440x1000。

用法：
    python ui_acceptance.py <stage>...
stage ∈ {item1, item2, item3, item4, item5, item6, all}

产物：
    <out>/shots/*.png        截图
    <out>/results-<stage>.json  逐项断言结果
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("SMD_UI_BASE", "http://127.0.0.1:8100")
USER = os.environ.get("SMD_UI_USER", "maint1")
PASSWORD = os.environ.get("SMD_UI_PASSWORD", "Pr84Ui#Accept2026")
FIRST_PASSWORD = os.environ.get("SMD_UI_FIRST_PASSWORD", "Pr84Ui#Accept2026")
OUT = Path(os.environ.get("SMD_UI_OUT", r"C:\Users\kevin\.cache\smd-pr84\out"))
SHOTS = OUT / "shots"
PROFILE = Path(os.environ.get("SMD_UI_PROFILE", r"C:\Users\kevin\.cache\smd-pr84\chrome-profile"))
EXT = Path(os.environ.get("SMD_UI_EXT", r"C:\Users\kevin\.cache\smd-pr84\zoom-ext"))

# 16 个页面（与 frontend/src/router/index.js 一一对应）
PAGES = [
    ("login", "/login", "anon"),
    ("change-password", "/change-password", "firstlogin"),
    ("overview", "/overview", "auth"),
    ("trend", "/trend", "auth"),
    ("test", "/test", "auth"),
    ("alarms", "/alarms", "auth"),
    ("history", "/history", "auth"),
    ("run-recoveries", "/run-recoveries", "auth"),
    ("recipes", "/recipes", "auth"),
    ("operations", "/operations", "auth"),
    ("parameters", "/parameters", "auth"),
    ("diagnostics", "/diagnostics", "auth"),
    ("settings", "/settings", "auth"),
    ("analytics", "/analytics", "auth"),
    ("reports", "/reports", "auth"),
    ("help", "/help", "auth"),
]

# ---------------------------------------------------------------- 浏览器辅助

READ_TOKENS_JS = """
() => {
  const root = document.documentElement;
  const saved = root.dataset.theme;
  const names = ['--bg-base','--bg-card','--bg-card2','--bg-hover','--bg-chrome','--border','--border-hi',
    '--text-pri','--text-sec','--text-muted','--accent','--accent-dim','--accent-soft','--green','--green-dim',
    '--yellow','--yellow-dim','--red','--red-dim','--red-soft','--orange','--purple','--success-text',
    '--warning-text','--danger-text','--on-orange','--on-red','--overlay','--chart-grid','--chart-tick',
    '--series-1','--series-2','--series-3','--series-4','--series-5','--series-6','--series-7','--series-8'];
  const grab = (t) => {
    root.dataset.theme = t;
    const cs = getComputedStyle(root);
    const out = {};
    for (const n of names) out[n] = cs.getPropertyValue(n).trim();
    return out;
  };
  const dark = grab('dark');
  const light = grab('light');
  root.dataset.theme = saved;
  return { dark, light };
}
"""

# 把页面上所有元素的前景/背景/边框色收集为规范化 rgb 列表
SCAN_COLORS_JS = """
() => {
  const seen = new Set();
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    for (const p of ['color','backgroundColor','borderTopColor','borderBottomColor',
                     'borderLeftColor','borderRightColor','outlineColor','boxShadow']) {
      const v = cs[p];
      if (!v) continue;
      if (p === 'boxShadow') {
        for (const m of v.matchAll(/rgba?\\([^)]*\\)/g)) seen.add(m[0]);
      } else {
        seen.add(v);
      }
    }
  }
  return [...seen];
}
"""

# 读取页面上每个 canvas 的实际像素颜色直方图（只保留出现次数足够多的颜色）
CANVAS_COLORS_JS = """
(minCount) => {
  const out = [];
  for (const c of document.querySelectorAll('canvas')) {
    const ctx = c.getContext('2d');
    if (!ctx || !c.width || !c.height) { out.push(null); continue; }
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    const hist = new Map();
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] < 200) continue;
      const k = (d[i] << 16) | (d[i + 1] << 8) | d[i + 2];
      hist.set(k, (hist.get(k) || 0) + 1);
    }
    const kept = [];
    for (const [k, n] of hist) if (n >= minCount) kept.push([k, n]);
    kept.sort((a, b) => b[1] - a[1]);
    out.push({ w: c.width, h: c.height, colors: kept.slice(0, 400) });
  }
  return out;
}
"""


def hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    v = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", v)
    if m:
        n = int(m.group(1), 16)
        return ((n >> 16) & 255, (n >> 8) & 255, n & 255)
    m = re.fullmatch(r"rgba?\(([^)]*)\)", v)
    if m:
        parts = [p.strip() for p in m.group(1).replace("/", " ").split(",")]
        if len(parts) >= 3:
            try:
                return tuple(int(round(float(p))) for p in parts[:3])  # type: ignore[return-value]
            except ValueError:
                return None
    return None


def norm_color(value: str) -> str | None:
    rgb = hex_to_rgb(value)
    return None if rgb is None else "%d,%d,%d" % rgb


def exclusive_tokens(tokens: dict) -> dict[str, dict[str, str]]:
    """返回各主题独有的颜色值（另一主题不存在），用于“旧色残留”判定。"""
    out = {}
    for theme, other in (("dark", "light"), ("light", "dark")):
        mine = {}
        other_vals = {norm_color(v) for v in tokens[other].values()}
        other_vals.discard(None)
        for name, raw in tokens[theme].items():
            c = norm_color(raw)
            if c and c not in other_vals:
                mine[c] = name
        out[theme] = mine
    return out


def close_rgb(a: tuple[int, int, int], b: tuple[int, int, int], tol: int) -> bool:
    return all(abs(a[i] - b[i]) <= tol for i in range(3))


# 窗口 1440x1000 DIP，并把设备像素比锁到 1，使 100% 档的 CSS 视口与 M1-05 基线
# （1440x913 / DPR 1）可比；浏览器缩放仍由扩展的 chrome.tabs.setZoom 真实设置。
BASE_ARGS = ["--window-size=1440,1000", "--window-position=0,0", "--force-device-scale-factor=1"]
CHANNEL = os.environ.get("SMD_UI_CHANNEL") or None


def launch(pw, *, with_extension: bool, **kwargs):
    args = list(BASE_ARGS)
    if with_extension:
        args += [f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"]
    return pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        channel=CHANNEL,
        headless=False,
        args=args,
        no_viewport=True,
        ignore_https_errors=True,
        **kwargs,
    )


def login(page, username: str = USER, password: str = PASSWORD) -> None:
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill("#login-username", username)
    page.fill("#login-password", password)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"/(overview|change-password)"), timeout=15000)


def logout(page) -> None:
    page.evaluate("() => { sessionStorage.clear(); }")
    page.goto(f"{BASE}/login", wait_until="networkidle")


def current_theme(page) -> str:
    return page.evaluate("() => document.documentElement.dataset.theme")


def click_theme_toggle(page) -> None:
    page.click("button.theme-toggle")
    page.wait_for_timeout(350)


def force_theme(page, theme: str) -> None:
    """非交互式设定主题（用于 item1 基线布置；item2 的换肤一律用按钮点击）。"""
    if current_theme(page) != theme:
        click_theme_toggle(page)
    assert current_theme(page) == theme, f"theme {current_theme(page)} != {theme}"


def settle(page, extra_ms: int = 900) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(extra_ms)


# ---------------------------------------------------------------- 页面布置


def prepare_page(page, name: str) -> dict:
    """为需要数据的页面制造可见内容；返回布置备注。"""
    note = {}
    if name == "analytics":
        # 只勾选合成夹具试验（T<日期>-NNN，各 180 点）；验收过程中真实下发的
        # TEST-<日期>-NNN 采样极少，画不出可辨识的曲线，不适合“8 色可区分”目检。
        rows = page.locator(".picker .pick")
        picked = []
        for i in range(rows.count()):
            label = (rows.nth(i).inner_text() or "").strip()
            if re.fullmatch(r"T\d{8}-\d{3}", label):
                rows.nth(i).locator("input[type=checkbox]").check()
                picked.append(label)
            if len(picked) == 8:
                break
        if len(picked) < 8:  # 夹具不足时退回“前 8 个”
            boxes = page.locator(".picker .pick input[type=checkbox]")
            for i in range(min(8, boxes.count())):
                if not boxes.nth(i).is_checked():
                    boxes.nth(i).check()
        note["selected_tests"] = picked
        page.click(".picker-foot button.primary")
        page.wait_for_selector(".chart-wrap canvas", timeout=15000)
        page.wait_for_timeout(1500)
    elif name == "parameters":
        inputs = page.locator(".field input:not([disabled])")
        changed = 0
        for i in range(min(3, inputs.count())):
            el = inputs.nth(i)
            raw = el.input_value()
            try:
                new = str(round(float(raw) + 5, 2))
            except ValueError:
                continue
            el.fill(new)
            el.dispatch_event("input")
            changed += 1
        note["changed_fields"] = changed
        page.wait_for_timeout(400)
    elif name == "alarms":
        page.wait_for_selector(".critical-banner", timeout=60000)
        note["critical_banner"] = True
    return note


# ---------------------------------------------------------------- item 1


SCROLL_INFO_JS = """
() => {
  const c = document.querySelector('.content');
  if (!c) return { container: null };
  return { container: '.content', scrollHeight: c.scrollHeight, clientHeight: c.clientHeight,
           overflow: c.scrollHeight > c.clientHeight + 2 };
}
"""


def capture(page, results, name: str, theme: str) -> None:
    """主截图为 1440 宽窗口的实际视口；内容区仍有下半屏时补一张滚动到底的 -b 图。"""
    page.evaluate("() => { const c = document.querySelector('.content'); if (c) c.scrollTop = 0; }")
    page.wait_for_timeout(250)
    shot = SHOTS / f"{name}-{theme}.png"
    page.screenshot(path=str(shot))
    results["shots"].append(shot.name)
    info = page.evaluate(SCROLL_INFO_JS)
    results.setdefault("scroll", {})[f"{name}-{theme}"] = info
    if info.get("overflow"):
        page.evaluate("() => { const c = document.querySelector('.content'); c.scrollTop = c.scrollHeight; }")
        page.wait_for_timeout(450)
        shot_b = SHOTS / f"{name}-{theme}-b.png"
        page.screenshot(path=str(shot_b))
        results["shots"].append(shot_b.name)
        page.evaluate("() => { const c = document.querySelector('.content'); c.scrollTop = 0; }")
        page.wait_for_timeout(200)


KEY_ELEMENTS_JS = """
() => {
  const probe = (sel) => {
    const els = [...document.querySelectorAll(sel)];
    if (!els.length) return null;
    const cs = getComputedStyle(els[0]);
    return { count: els.length, background: cs.backgroundColor, color: cs.color,
             borderColor: cs.borderTopColor, animation: cs.animationName,
             text: (els[0].innerText || '').trim().slice(0, 30) };
  };
  return {
    '.co-tag': probe('.co-tag'),
    '.dot-green': probe('.dot-green'),
    '.dot-red': probe('.dot-red'),
    '.dot-yellow': probe('.dot-yellow'),
    '.critical-banner': probe('.critical-banner'),
    '.step.co .co-tag': probe('.step.co .co-tag'),
    '.field input.changed': probe('.field input.changed'),
    '.state-badge': probe('.state-badge'),
    '.kpi-val': probe('.kpi-val'),
  };
}
"""


def item1(ctx) -> dict:
    page = ctx.pages[0]
    results = {"shots": [], "notes": {}, "key_elements": {}}

    # 1) 登录页（未登录）
    logout(page)
    for theme in ("dark", "light"):
        force_theme(page, theme)
        settle(page)
        capture(page, results, "login", theme)

    # 2) 修改初始密码页（首次登录账号 operator1）
    login(page, "operator1", FIRST_PASSWORD)
    page.wait_for_url(re.compile(r"/change-password"), timeout=15000)
    for theme in ("dark", "light"):
        force_theme(page, theme)
        settle(page)
        capture(page, results, "change-password", theme)
    logout(page)

    # 3) 其余 14 页（maintainer 账号，可见全部导航）
    login(page)
    for name, path, kind in PAGES:
        if kind != "auth":
            continue
        page.goto(f"{BASE}{path}", wait_until="domcontentloaded")
        settle(page)
        note = prepare_page(page, name)
        if note:
            results["notes"][name] = note
        for theme in ("dark", "light"):
            force_theme(page, theme)
            settle(page, 700)
            capture(page, results, name, theme)
            probed = {k: v for k, v in page.evaluate(KEY_ELEMENTS_JS).items() if v}
            if probed:
                results["key_elements"][f"{name}-{theme}"] = probed
    results["tokens"] = page.evaluate(READ_TOKENS_JS)
    return results


# ---------------------------------------------------------------- item 2


def item2(ctx) -> dict:
    page = ctx.pages[0]
    login(page)
    tokens = page.evaluate(READ_TOKENS_JS)
    excl = exclusive_tokens(tokens)
    out = {"tokens": tokens, "pages": {}}

    auth_pages = [p for p in PAGES if p[2] == "auth"]
    for idx, (name, path, _kind) in enumerate(auth_pages):
        page.goto(f"{BASE}{path}", wait_until="domcontentloaded")
        settle(page)
        prepare_page(page, name)
        # 交替起始主题，使 dark→light 与 light→dark 两个方向都被覆盖
        force_theme(page, "dark" if idx % 2 == 0 else "light")
        settle(page, 600)
        before_theme = current_theme(page)
        after_theme = "light" if before_theme == "dark" else "dark"

        before_css = set(filter(None, (norm_color(c) for c in page.evaluate(SCAN_COLORS_JS))))
        before_canvas = page.evaluate(CANVAS_COLORS_JS, 60)

        click_theme_toggle(page)
        settle(page, 900)
        assert current_theme(page) == after_theme

        after_css = set(filter(None, (norm_color(c) for c in page.evaluate(SCAN_COLORS_JS))))
        after_canvas = page.evaluate(CANVAS_COLORS_JS, 60)

        stale_css = sorted(
            f"{c}({excl[before_theme][c]})" for c in after_css if c in excl[before_theme]
        )
        applied_css = sorted(c for c in after_css if c in excl[after_theme])

        canvas_report = []
        for idx, snap in enumerate(after_canvas):
            if not snap:
                canvas_report.append(None)
                continue
            painted = [((k >> 16) & 255, (k >> 8) & 255, k & 255) for k, _ in snap["colors"]]
            series_new, series_old, stale_hits = [], [], []
            for i in range(1, 9):
                new_rgb = hex_to_rgb(tokens[after_theme][f"--series-{i}"])
                old_rgb = hex_to_rgb(tokens[before_theme][f"--series-{i}"])
                hit_new = sum(n for (k, n) in snap["colors"]
                              if close_rgb(((k >> 16) & 255, (k >> 8) & 255, k & 255), new_rgb, 6))
                hit_old = sum(n for (k, n) in snap["colors"]
                              if close_rgb(((k >> 16) & 255, (k >> 8) & 255, k & 255), old_rgb, 6))
                series_new.append(hit_new)
                series_old.append(hit_old)
                if hit_old > 0:
                    stale_hits.append(f"--series-{i}")
            canvas_report.append({
                "size": [snap["w"], snap["h"]],
                "distinct_painted": len(painted),
                "series_new_pixels": series_new,
                "series_old_pixels": series_old,
                "stale_series": stale_hits,
            })

        before_canvas_sig = [None if not s else len(s["colors"]) for s in before_canvas]
        out["pages"][name] = {
            "from": before_theme,
            "to": after_theme,
            "css_colors_before": len(before_css),
            "css_colors_after": len(after_css),
            "stale_exclusive_css": stale_css,
            "applied_exclusive_css_count": len(applied_css),
            "canvas_count": len(after_canvas),
            "canvas_before_distinct": before_canvas_sig,
            "canvas": canvas_report,
            "pass": not stale_css and all((c is None or not c["stale_series"]) for c in canvas_report),
        }

    # 数据分析叠加图的专项：两个方向各切一次并存证截图
    page.goto(f"{BASE}/analytics", wait_until="domcontentloaded")
    settle(page)
    prepare_page(page, "analytics")
    force_theme(page, "dark")
    settle(page, 1200)
    page.locator(".chart-wrap").screenshot(path=str(SHOTS / "item2-analytics-overlay-dark.png"))
    click_theme_toggle(page)
    settle(page, 1200)
    page.locator(".chart-wrap").screenshot(path=str(SHOTS / "item2-analytics-overlay-light.png"))
    click_theme_toggle(page)
    settle(page, 1200)
    page.locator(".chart-wrap").screenshot(path=str(SHOTS / "item2-analytics-overlay-back-dark.png"))
    return out


# ---------------------------------------------------------------- item 3


def ensure_extension() -> None:
    EXT.mkdir(parents=True, exist_ok=True)
    (EXT / "manifest.json").write_text(json.dumps({
        "manifest_version": 3,
        "name": "smd-pr84-zoom",
        "version": "1.0",
        "permissions": ["tabs"],
        "host_permissions": ["<all_urls>"],
        "background": {"service_worker": "bg.js"},
    }), encoding="utf-8")
    (EXT / "bg.js").write_text(
        "chrome.runtime.onInstalled.addListener(() => {});\n"
        "self.addEventListener('activate', () => {});\n",
        encoding="utf-8",
    )


def set_zoom(ctx, factor: float) -> float:
    workers = ctx.service_workers
    if not workers:
        workers = [ctx.wait_for_event("serviceworker", timeout=20000)]
    sw = workers[0]
    return sw.evaluate(
        """async (z) => {
            const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
            await chrome.tabs.setZoom(t.id, z);
            return await chrome.tabs.getZoom(t.id);
        }""",
        factor,
    )


def item3(ctx) -> dict:
    page = ctx.pages[0]
    page.bring_to_front()
    login(page)
    out = {"targets": {}}
    targets = [("overview", "/overview"), ("history", "/history"), ("parameters", "/parameters")]
    for zoom in (1.0, 1.25, 1.5, 2.0):
        read_back = set_zoom(ctx, zoom)
        for name, path in targets:
            page.goto(f"{BASE}{path}", wait_until="domcontentloaded")
            settle(page)
            if name == "parameters":
                prepare_page(page, name)
            metrics = page.evaluate(
                """() => {
                    const d = document.documentElement;
                    return {
                        scrollWidth: d.scrollWidth,
                        clientWidth: d.clientWidth,
                        innerWidth: window.innerWidth,
                        innerHeight: window.innerHeight,
                        dpr: window.devicePixelRatio,
                        visualScale: window.visualViewport ? window.visualViewport.scale : null,
                        bodyScrollWidth: document.body.scrollWidth,
                        bodyClientWidth: document.body.clientWidth,
                    };
                }"""
            )
            metrics["no_horizontal_overflow"] = metrics["scrollWidth"] <= metrics["clientWidth"]
            metrics["zoom_readback"] = read_back
            out["targets"].setdefault(f"{int(zoom * 100)}%", {})[name] = metrics
            page.screenshot(path=str(SHOTS / f"item3-{name}-zoom{int(zoom * 100)}.png"))
    set_zoom(ctx, 1.0)
    return out


# ---------------------------------------------------------------- item 4

FOCUS_JS = """
() => {
  const a = document.activeElement;
  if (!a) return null;
  return {
    tag: a.tagName,
    id: a.id || null,
    text: (a.innerText || a.value || '').trim().slice(0, 40),
    cls: a.className && a.className.baseVal === undefined ? String(a.className) : '',
    hasCancelAttr: a.hasAttribute('data-dialog-cancel'),
    inDialog: Boolean(a.closest('[role=dialog]')),
    dialogLabel: (() => {
      const d = a.closest('[role=dialog]');
      if (!d) return null;
      const t = d.querySelector('.dlg-title');
      return t ? t.innerText.trim() : null;
    })(),
  };
}
"""

DIALOG_COUNT_JS = "() => document.querySelectorAll('[role=dialog]').length"


def focus_info(page):
    return page.evaluate(FOCUS_JS)


def tab_cycle(page, steps: int, shift: bool = False):
    seq = []
    for _ in range(steps):
        page.keyboard.press("Shift+Tab" if shift else "Tab")
        page.wait_for_timeout(90)
        seq.append(focus_info(page))
    return seq


def group_stop_test(page) -> dict:
    page.goto(f"{BASE}/test", wait_until="domcontentloaded")
    settle(page)
    trigger = page.locator("button.danger", has_text="停止试验").first
    trigger.scroll_into_view_if_needed()
    trigger.focus()
    opener = focus_info(page)
    trigger.click()
    page.wait_for_selector("[role=dialog]", timeout=8000)
    page.wait_for_timeout(400)
    rec = {
        "opener": opener,
        "dialogs_open": page.evaluate(DIALOG_COUNT_JS),
        "focus_on_open": focus_info(page),
        "tab_forward": tab_cycle(page, 8),
        "tab_backward": tab_cycle(page, 8, shift=True),
    }
    page.screenshot(path=str(SHOTS / "item4-stop-dialog.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    rec["dialogs_after_esc"] = page.evaluate(DIALOG_COUNT_JS)
    rec["focus_after_close"] = focus_info(page)
    return rec


def group_start_test(page) -> dict:
    page.goto(f"{BASE}/test", wait_until="domcontentloaded")
    settle(page)
    trigger = page.locator("button.primary", has_text="启动试验").first
    trigger.scroll_into_view_if_needed()
    trigger.focus()
    opener = focus_info(page)
    trigger.click()
    page.wait_for_selector("[role=dialog]", timeout=8000)
    page.wait_for_timeout(400)
    rec = {
        "opener": opener,
        "outer_dialogs_open": page.evaluate(DIALOG_COUNT_JS),
        "outer_focus_on_open": focus_info(page),
        "outer_tab_forward": tab_cycle(page, 10),
    }
    # 填必填项后进入嵌套确认
    page.fill("#start-test-height", "75.0")
    page.wait_for_timeout(200)
    next_btn = page.locator(".actions button.primary", has_text="下一步").first
    next_btn.focus()
    rec["nested_opener"] = focus_info(page)
    next_btn.click()
    page.wait_for_timeout(600)
    rec["nested_dialogs_open"] = page.evaluate(DIALOG_COUNT_JS)
    rec["nested_focus_on_open"] = focus_info(page)
    rec["nested_tab_forward"] = tab_cycle(page, 6)
    rec["nested_tab_backward"] = tab_cycle(page, 6, shift=True)
    page.screenshot(path=str(SHOTS / "item4-start-nested-dialog.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    rec["dialogs_after_first_esc"] = page.evaluate(DIALOG_COUNT_JS)
    rec["focus_after_first_esc"] = focus_info(page)
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    rec["dialogs_after_second_esc"] = page.evaluate(DIALOG_COUNT_JS)
    rec["focus_after_second_esc"] = focus_info(page)
    return rec


def group_password_reset(page) -> dict:
    page.goto(f"{BASE}/settings", wait_until="domcontentloaded")
    settle(page)
    trigger = page.locator("button", has_text="重置密码").first
    trigger.scroll_into_view_if_needed()
    trigger.focus()
    opener = focus_info(page)
    trigger.click()
    page.wait_for_selector("[role=dialog]", timeout=8000)
    page.wait_for_timeout(400)
    rec = {
        "opener": opener,
        "dialogs_open": page.evaluate(DIALOG_COUNT_JS),
        "focus_on_open": focus_info(page),
        "tab_forward": tab_cycle(page, 8),
        "tab_backward": tab_cycle(page, 8, shift=True),
    }
    page.screenshot(path=str(SHOTS / "item4-password-reset-dialog.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    rec["dialogs_after_esc"] = page.evaluate(DIALOG_COUNT_JS)
    rec["focus_after_close"] = focus_info(page)
    return rec


def group_parameters_confirm(page) -> dict:
    """M1-05 基线第四组“准备离线升级”入口在 ADR-011 后已从 UI 移除，
    本组改测同页面族里仍存在的受控写入二次确认（参数下发），并单独记录该差异。"""
    page.goto(f"{BASE}/parameters", wait_until="domcontentloaded")
    settle(page)
    prepare_page(page, "parameters")
    trigger = page.locator(".actions button.primary").first
    trigger.scroll_into_view_if_needed()
    disabled = trigger.is_disabled()
    trigger.focus()
    opener = focus_info(page)
    rec = {"opener": opener, "trigger_disabled": disabled}
    if disabled:
        rec["skipped"] = "确认按钮受设备 can_set_parameters 门控，未打开弹窗"
        return rec
    trigger.click()
    page.wait_for_selector("[role=dialog]", timeout=8000)
    page.wait_for_timeout(400)
    rec.update({
        "dialogs_open": page.evaluate(DIALOG_COUNT_JS),
        "focus_on_open": focus_info(page),
        "tab_forward": tab_cycle(page, 6),
        "tab_backward": tab_cycle(page, 6, shift=True),
    })
    page.screenshot(path=str(SHOTS / "item4-parameters-confirm-dialog.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    rec["dialogs_after_esc"] = page.evaluate(DIALOG_COUNT_JS)
    rec["focus_after_close"] = focus_info(page)
    return rec


def start_test_for_real(page) -> dict:
    """通过 UI 真实下发一次 start_test（对象是本地 Mock 控制板，不接真实设备），
    使“停止试验”组具备可用的前置状态。"""
    page.goto(f"{BASE}/test", wait_until="domcontentloaded")
    settle(page)
    page.locator("button.primary", has_text="启动试验").first.click()
    page.wait_for_selector("#start-test-height", timeout=8000)
    page.fill("#start-test-height", "75.0")
    # 展开配方选择并选中已下发的标准模板版本（后端要求 start_test 绑定配方版本）
    summary = page.locator("[role=dialog] details summary", has_text="选择已下发配方").first
    if summary.count() and not page.locator("[role=dialog] details[open]").count():
        summary.click()
        page.wait_for_timeout(300)
    recipe_select = page.locator('[role=dialog] select[aria-label="已保存配方"]').first
    recipe_select.wait_for(timeout=8000)
    options = recipe_select.locator("option")
    picked = None
    for i in range(options.count()):
        value = options.nth(i).get_attribute("value")
        if value:
            recipe_select.select_option(value)
            picked = value
            break
    page.wait_for_timeout(900)
    page.screenshot(path=str(SHOTS / "item4-start-modal-recipe.png"))
    page.locator(".actions button.primary", has_text="下一步").first.click()
    page.wait_for_timeout(500)
    page.locator("[role=dialog] button.danger", has_text="确认启动").first.click()
    page.wait_for_timeout(2500)
    settle(page)
    state = page.evaluate("() => document.querySelector('.state-badge')?.innerText || ''")
    stop_enabled = not page.locator("button.danger", has_text="停止试验").first.is_disabled()
    return {"recipe_option": picked, "state_after_start": state, "stop_button_enabled": stop_enabled}


def stop_test_for_real(page) -> dict:
    page.goto(f"{BASE}/test", wait_until="domcontentloaded")
    settle(page)
    btn = page.locator("button.danger", has_text="停止试验").first
    if btn.is_disabled():
        return {"skipped": "停止按钮不可用"}
    btn.click()
    page.wait_for_selector("[role=dialog]", timeout=8000)
    page.locator("[role=dialog] button.danger", has_text="确认停止").first.click()
    page.wait_for_timeout(2500)
    settle(page)
    return {"state_after_stop": page.evaluate("() => document.querySelector('.state-badge')?.innerText || ''")}


def item4(ctx) -> dict:
    page = ctx.pages[0]
    page.bring_to_front()
    login(page)
    force_theme(page, "dark")
    out = {"start_test_nested": group_start_test(page)}
    out["password_reset"] = group_password_reset(page)
    # 参数下发确认需在非运行状态执行（can_set_parameters 门控），故排在启动之前
    out["parameters_confirm"] = group_parameters_confirm(page)
    out["offline_upgrade_entry"] = probe_offline_upgrade_entry(page)
    out["_prepare_running_test"] = start_test_for_real(page)
    out["stop_test"] = group_stop_test(page)
    out["_cleanup_stop_test"] = stop_test_for_real(page)
    return out


def probe_offline_upgrade_entry(page) -> dict:
    """M1-05 基线第四组“准备离线升级”弹窗：核对被测构建里是否还有该入口。"""
    hits = []
    for name, path, kind in PAGES:
        if kind != "auth":
            continue
        page.goto(f"{BASE}{path}", wait_until="domcontentloaded")
        settle(page, 500)
        found = page.evaluate(
            """() => [...document.querySelectorAll('button, a, summary, [role=button]')]
                    .map(el => (el.innerText || '').trim())
                    .filter(t => /准备升级|准备离线升级|离线升级|升级准备/.test(t))"""
        )
        if found:
            hits.append({"page": name, "controls": found})
    page.goto(f"{BASE}/settings", wait_until="domcontentloaded")
    settle(page, 500)
    maintenance_text = page.evaluate(
        "() => document.querySelector('.maintenance')?.innerText.replace(/\\s+/g, ' ').slice(0, 300) || null"
    )
    page.screenshot(path=str(SHOTS / "item4-settings-maintenance-panel.png"))
    return {"controls_found": hits, "maintenance_panel_text": maintenance_text}


# ---------------------------------------------------------------- item 5

MOTION_PROBE_JS = """
() => {
  const pick = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const cs = getComputedStyle(el);
    return {
      selector: sel,
      animationName: cs.animationName,
      animationDuration: cs.animationDuration,
      animationIterationCount: cs.animationIterationCount,
      transitionDuration: cs.transitionDuration,
    };
  };
  return {
    matchesReduce: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    dotGreen: pick('.dot-green'),
    dotRed: pick('.dot-red'),
    dotAny: pick('.dot'),
    criticalBanner: pick('.critical-banner'),
  };
}
"""


def item5(ctx, pw) -> dict:
    out = {}
    for mode in ("no-preference", "reduce"):
        ctx2 = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE.parent / f"chrome-profile-motion-{mode}"),
            channel=CHANNEL,
            headless=False,
            args=list(BASE_ARGS),
            no_viewport=True,
            reduced_motion="reduce" if mode == "reduce" else "no-preference",
        )
        try:
            page = ctx2.pages[0]
            login(page)
            force_theme(page, "dark")
            page.goto(f"{BASE}/overview", wait_until="domcontentloaded")
            settle(page)
            probe_overview = page.evaluate(MOTION_PROBE_JS)
            page.screenshot(path=str(SHOTS / f"item5-overview-{mode}.png"), full_page=True)
            page.goto(f"{BASE}/alarms", wait_until="domcontentloaded")
            settle(page)
            page.wait_for_selector(".critical-banner", timeout=60000)
            probe_alarms = page.evaluate(MOTION_PROBE_JS)
            page.screenshot(path=str(SHOTS / f"item5-alarms-{mode}.png"), full_page=True)
            out[mode] = {"overview": probe_overview, "alarms": probe_alarms}
        finally:
            ctx2.close()
    return out


# ---------------------------------------------------------------- item 6

FONT_PROBE_JS = """
(selectors) => {
  const probe = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const cs = getComputedStyle(el);
    // 用 Canvas 度量确认实际命中的等宽字体：同宽的 i/W 说明是等宽渲染
    const c = document.createElement('canvas').getContext('2d');
    const fam = cs.fontFamily;
    c.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${fam}`;
    const widths = {};
    for (const ch of ['i', 'W', '0', '8']) widths[ch] = c.measureText(ch).width;
    const mono = Math.abs(widths.i - widths.W) < 0.01 && Math.abs(widths['0'] - widths['8']) < 0.01;
    const first = (name) => {
      const c2 = document.createElement('canvas').getContext('2d');
      c2.font = `${cs.fontSize} ${name}`;
      return c2.measureText('0123456789ABCDEFghij').width;
    };
    return {
      selector: sel,
      text: (el.innerText || '').trim().slice(0, 40),
      fontFamily: fam,
      fontSize: cs.fontSize,
      widths,
      monospaced: mono,
      widthAs: {
        cascadia: first("'Cascadia Mono'"),
        consolas: first('Consolas'),
        courierNew: first("'Courier New'"),
        actual: (() => { const c3 = document.createElement('canvas').getContext('2d');
                         c3.font = `${cs.fontSize} ${fam}`;
                         return c3.measureText('0123456789ABCDEFghij').width; })(),
      },
    };
  };
  return selectors.map(probe);
}
"""


def shoot_element(page, selector: str, filename: str) -> bool:
    """存在才截；缺元素不应让整阶段失败。"""
    loc = page.locator(selector).first
    if not loc.count():
        return False
    try:
        loc.scroll_into_view_if_needed(timeout=4000)
        loc.screenshot(path=str(SHOTS / filename), timeout=8000)
        return True
    except Exception:
        return False


def item6(ctx) -> dict:
    page = ctx.pages[0]
    page.bring_to_front()
    login(page)
    force_theme(page, "dark")
    out = {}

    page.goto(f"{BASE}/overview", wait_until="domcontentloaded")
    settle(page)
    out["overview"] = page.evaluate(FONT_PROBE_JS, [".kpi-val", ".kpi-primary .kpi-val", ".mono"])
    page.screenshot(path=str(SHOTS / "item6-overview-kpi.png"))
    out.setdefault("element_shots", {})["kpi"] = shoot_element(page, ".kpi-grid", "item6-overview-kpi-zoom.png")

    page.goto(f"{BASE}/history", wait_until="domcontentloaded")
    settle(page)
    out["history"] = page.evaluate(FONT_PROBE_JS, [".mono", "td.mono", ".data-table .mono"])
    page.screenshot(path=str(SHOTS / "item6-history-ids.png"))
    out["element_shots"]["history"] = shoot_element(page, "table", "item6-history-ids-zoom.png")

    page.goto(f"{BASE}/test", wait_until="domcontentloaded")
    settle(page)
    out["test"] = page.evaluate(FONT_PROBE_JS, [".test-id", ".elapsed", ".proc-val"])
    page.screenshot(path=str(SHOTS / "item6-test-mono.png"))
    out["element_shots"]["proc"] = shoot_element(page, ".card.proc", "item6-test-proc-zoom.png")

    # 同字号下三种等宽候选的并排渲染样张，供人工核对实际命中的是 Cascadia Mono
    page.goto(f"{BASE}/overview", wait_until="domcontentloaded")
    settle(page, 400)
    page.evaluate(
        """() => {
            const box = document.createElement('div');
            box.id = 'pr84-font-sample';
            box.style.cssText = 'position:fixed;left:24px;top:80px;z-index:9999;padding:14px 18px;'
              + 'background:var(--bg-card);border:1px solid var(--border-hi);border-radius:10px;'
              + 'color:var(--text-pri);font-size:20px;line-height:1.9';
            const rows = [
              ['var(--font-mono) [实际生效]', 'var(--font-mono)'],
              ['Cascadia Mono', "'Cascadia Mono'"],
              ['Consolas', 'Consolas'],
              ['Courier New', "'Courier New'"],
            ];
            box.innerHTML = rows.map(([label, fam]) =>
              `<div style="font-family:${fam}">1580.0 ℃ TEST-20260921-001 2026-09-21 12:00:00 &nbsp;<span style="font-size:12px;opacity:.7">${label}</span></div>`
            ).join('');
            document.body.appendChild(box);
        }"""
    )
    page.wait_for_timeout(400)
    out["element_shots"]["font_sample"] = shoot_element(page, "#pr84-font-sample", "item6-font-stack-compare.png")
    page.evaluate("() => document.getElementById('pr84-font-sample')?.remove()")

    page.goto(f"{BASE}/alarms", wait_until="domcontentloaded")
    settle(page)
    out["alarms"] = page.evaluate(FONT_PROBE_JS, [".mono", "td.mono"])

    out["available_fonts"] = page.evaluate(
        """async () => {
            const names = ['Cascadia Mono', 'Consolas', 'Courier New', 'Segoe UI'];
            const out = {};
            for (const n of names) out[n] = document.fonts.check(`12px "${n}"`);
            return out;
        }"""
    )
    return out


# ---------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    stages = argv or ["all"]
    if "all" in stages:
        stages = ["item1", "item2", "item3", "item4", "item5", "item6"]
    SHOTS.mkdir(parents=True, exist_ok=True)
    ensure_extension()
    results = {}
    with sync_playwright() as pw:
        for stage in stages:
            started = time.time()
            if stage == "item5":
                results[stage] = item5(None, pw)
            else:
                ctx = launch(pw, with_extension=(stage == "item3"))
                try:
                    probe = ctx.pages[0]
                    probe.goto(f"{BASE}/login", wait_until="domcontentloaded")
                    env = {
                        "browser_version": ctx.browser.version if ctx.browser else None,
                        "channel": CHANNEL or "playwright-bundled-chromium",
                        "user_agent": probe.evaluate("() => navigator.userAgent"),
                        "viewport": probe.evaluate(
                            "() => [innerWidth, innerHeight, devicePixelRatio, screen.width, screen.height]"
                        ),
                    }
                    results[stage] = {
                        "item1": item1,
                        "item2": item2,
                        "item3": item3,
                        "item4": item4,
                        "item6": item6,
                    }[stage](ctx)
                    results[stage]["_env"] = env
                finally:
                    ctx.close()
            results[stage]["_elapsed_s"] = round(time.time() - started, 1)
            path = OUT / f"results-{stage}.json"
            path.write_text(json.dumps(results[stage], ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[{stage}] done in {results[stage]['_elapsed_s']}s -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
