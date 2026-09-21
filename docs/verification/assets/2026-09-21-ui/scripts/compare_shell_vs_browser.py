"""第 7 项：对 WebView2 桌面壳与浏览器入口的截图做客观取色比对。

两侧窗口尺寸与标题栏不同，逐像素 diff 没有意义；这里统计每张图里出现的主题令牌色
（theme.css 的 dark/light 取值），验证同一页面同一主题下两个入口用的是同一套颜色，
并确认没有混进另一主题的独有色。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from PIL import Image

SHOTS = Path(r"C:\Users\kevin\.cache\smd-pr84\out\shots")

# 取自 frontend/src/assets/theme.css（0515727）
TOKENS = {
    "dark": {
        "--bg-base": "#0b0e15", "--bg-card": "#151a24", "--bg-chrome": "#10141d",
        "--text-pri": "#e8edf6", "--accent": "#38bdf8", "--green": "#22c55e",
        "--orange": "#f97316", "--red": "#ef4444", "--series-1": "#3987e5",
    },
    "light": {
        "--bg-base": "#f2f5f9", "--bg-card": "#ffffff", "--bg-chrome": "#ffffff",
        "--text-pri": "#18202f", "--accent": "#0369a1", "--green": "#15803d",
        "--orange": "#c2410c", "--red": "#dc2626", "--series-1": "#2a78d6",
    },
}

PAIRS = [
    ("overview", "dark"),
    ("overview", "light"),
]


def rgb(value: str) -> tuple[int, int, int]:
    n = int(value.lstrip("#"), 16)
    return ((n >> 16) & 255, (n >> 8) & 255, n & 255)


def histogram(path: Path) -> Counter:
    with Image.open(path) as im:
        return Counter(im.convert("RGB").getdata())


# PowerShell 的 CopyFromScreen 走显示管线，颜色会有 ±1~3 的偏移（Playwright 截图是
# 合成器直出且固定 sRGB），因此按容差匹配，并记下最接近的实际像素值。
TOLERANCE = 6


def count_near(hist: Counter, target: tuple[int, int, int], tol: int = TOLERANCE) -> tuple[int, tuple | None]:
    total = 0
    best = None
    best_n = 0
    for color, n in hist.items():
        if all(abs(color[i] - target[i]) <= tol for i in range(3)):
            total += n
            if n > best_n:
                best_n, best = n, color
    return total, best


def report(path: Path, theme: str) -> dict:
    hist = histogram(path)
    own = {}
    nearest = {}
    for name, v in TOKENS[theme].items():
        n, best = count_near(hist, rgb(v))
        own[name] = n
        nearest[name] = {"token": v, "observed": best}
    other = "light" if theme == "dark" else "dark"
    exclusive_other = {
        name: count_near(hist, rgb(v))[0]
        for name, v in TOKENS[other].items()
        if all(any(abs(rgb(v)[i] - rgb(x)[i]) > TOLERANCE for i in range(3)) for x in TOKENS[theme].values())
    }
    with Image.open(path) as im:
        size = im.size
    return {
        "file": path.name,
        "size": size,
        "tolerance": TOLERANCE,
        "own_theme_pixels": own,
        "nearest_observed": nearest,
        "own_theme_all_present": all(c > 0 for c in own.values()),
        "other_theme_exclusive_pixels": exclusive_other,
        "other_theme_leak": {k: v for k, v in exclusive_other.items() if v > 0},
    }


def main() -> None:
    out = {}
    for page, theme in PAIRS:
        entry = {}
        for side in ("shell", "browser-gdi", "browser"):
            path = SHOTS / f"item7-{side}-{page}-{theme}.png"
            entry[side] = report(path, theme) if path.is_file() else {"missing": str(path)}
        # 同口径判定只比较两张都经 GDI 屏幕采集的图；Playwright 直出图另列作参考。
        a, b = entry.get("shell"), entry.get("browser-gdi")
        if "nearest_observed" in a and "nearest_observed" in b:
            deltas = {}
            for name in TOKENS[theme]:
                oa = a["nearest_observed"][name]["observed"]
                ob = b["nearest_observed"][name]["observed"]
                deltas[name] = None if (oa is None or ob is None) else max(abs(oa[i] - ob[i]) for i in range(3))
            entry["gdi_channel_delta"] = deltas
            entry["gdi_max_delta"] = max([d for d in deltas.values() if d is not None] or [None])
            entry["gdi_all_tokens_found"] = all(d is not None for d in deltas.values())
        out[f"{page}-{theme}"] = entry
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
