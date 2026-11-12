"""
Competitive pain-point analysis of Thai Google Play reviews.

Rule-based, multi-label tagging with Thai + English keyword lexicons.
Transparent and auditable by design: every tag can be traced to a keyword,
and tagging accuracy is spot-checked on a random sample (see docs).

Input : data/external/reviews_raw.csv  (from src/collect_reviews.py, git-ignored)
Output: data/processed/review_tags.csv (git-ignored)
        reports/painpoints_summary.csv  (aggregated, safe to publish)
"""
from __future__ import annotations

import re
import warnings

warnings.filterwarnings("ignore", message="This pattern is interpreted")
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "external" / "reviews_raw.csv"

# --------------------------------------------------------------- lexicons
# Order does not matter (multi-label). Keep patterns specific; broad words
# like "โหลด" or "จอ" are only used inside longer phrases to avoid false hits.
PAINPOINTS: dict[str, list[str]] = {
    # paid (or subscribed) but the entitlement did not work
    "paid_no_access": [
        r"(จ่าย|ชำระ|สมัคร|หัก|ตัด|เติม|เสีย)(เงิน|ตัง)?.{0,25}(ดูไม่ได้|เข้าไม่ได้|ใช้ไม่ได้|ไม่ได้ดู|ดูอะไรไม่ได้)",
        r"vip.{0,20}(ไม่(ขึ้น|เป็น|ได้|สามารถ)|ยังไม่)", r"(ระงับ|บล็?[อ๊]ค).{0,10}(บัญชี|ไอดี|id)",
        r"already pa(y|id).{0,20}can'?t",
    ],
    "billing_unexpected_charge": [
        r"หักเงิน", r"หักตัง", r"ตัดเงิน", r"ตัดบัตร", r"เรียกเก็บ", r"คืนเงิน", r"เงินคืน",
        r"โดนหัก", r"หักไป", r"หักซ้ำ", r"ดูดตัง", r"refund", r"charged",
    ],
    "cancel_difficulty": [
        r"ยกเลิก.{0,20}(ยาก|ไม่ได้|ไม่ทัน|ไม่เป็น)", r"(หา|วิธี)(วิธี)?ยกเลิก",
        r"ยกเลิก(ไป)?แล้ว.{0,30}(หัก|ตัด|เรียกเก็บ|ดูด)", r"can'?t cancel",
    ],
    "login_account": [
        r"เข้าสู่ระบบ", r"ล็อ[คก]อิน", r"login", r"log in", r"sign ?in", r"otp", r"รหัส",
        r"บัญชี", r"อีเมล", r"ลงทะเบียน", r"สมัคร(สมาชิก)?(ไม่ได้|ไม่ผ่าน|ยาก|ล้มเหลว)",
        r"เข้า(แอ[พป]|ระบบ)?ไม่ได้",
    ],
    "playback_stability": [
        r"ค้าง", r"กระตุก", r"สะดุด", r"จอดำ", r"หน้าจอดำ", r"โหลดไม่", r"โหลดช้า", r"หมุน", r"บัฟ",
        r"buffer", r"เด้งออก", r"ปิดเอง", r"ปิดตัวเอง", r"แครช", r"crash", r"error", r"ข้อผิดพลาด",
        r"เล่นไม่ได้", r"เสียงแตก", r"ภาพแตก", r"ภาพไม่ชัด", r"ไม่ลื่น", r"หลุดบ่อย", r"ไม่เสถียร",
        r"บั[คก]", r"\bbugs?\b", r"แอ[พป]ดับ",
    ],
    "ads_intrusive": [r"โฆษณา", r"โคสนา", r"โฆสนา", r"\bads?\b", r"advert",
                      r"เด้งไป.{0,10}(shopee|ช็อปปี้|ช็อบปี้|tiktok|ติ๊กต่อก|แอ[พป]อื่น|ที่อื่น)"],
    "price_paywall": [
        r"แพง", r"ขึ้นราคา", r"ราคา", r"จ่ายเพิ่ม", r"เสียเงินเพิ่ม", r"ต้องเสียเงิน",
        r"บังคับ(ให้)?(เติม|ซื้อ|สมัคร|จ่าย)", r"express", r"เก็บยิบย่อย",
        r"ล็อ[คก].{0,15}(ตอน|เรื่อง|หนัง)", r"ดูฟรี.{0,15}(ไม่|น้อย)", r"svip", r"expensive", r"price",
    ],
    "content_catalogue": [
        r"ไม่มีเรื่อง", r"เรื่องน้อย", r"หนังน้อย", r"ไม่มีหนัง", r"(เรื่อง|ซีรี|หนัง|ตอน).{0,15}(หายไป|ถูกลบ|เอาออก)",
        r"(ไม่มี|ขาด)(หนัง|เรื่อง|ซีรี).{0,5}ใหม่", r"ตอนใหม่.{0,10}(ช้า|ไม่)", r"อั[พป]ตอน.{0,10}(ช้า|ไม่)",
        r"เนื้อหา(น้อย|ไม่ครบ)", r"คอนเทนต์", r"content", r"ไม่มีให้ดู", r"(ดู|ใช้)ไม่ได้ในประเทศ",
    ],
    "subtitle_dubbing": [r"ซับ", r"บรรยายไทย", r"คำบรรยาย", r"พากย์", r"พากษ์", r"subtitle", r"เสียงไทย", r"\bdub"],
    "tv_device_support": [r"ทีวี", r"\btv\b", r"android ?tv", r"smart ?tv", r"chromecast", r"แคส", r"\bcast\b"],
    "app_update_ux": [r"อั[พป]เด[ตท]", r"update", r"เวอร์ชั่น", r"เวอร์ชัน", r"ใช้ยาก", r"หน้าตาแอ[พป]", r"แอ[พป]ใหม่", r"\bux\b", r"\bui\b"],
    "payment_methods": [r"บัตรเครดิต", r"เดบิต", r"ทรูมัน", r"true ?money", r"พร้อมเพย์", r"promptpay", r"คิวอาร์|qr ?code",
                        r"mobile banking", r"ช่องทาง(การ)?(ชำระ|จ่าย)", r"วิธี(การ)?(ชำระ|จ่าย)", r"(ชำระ|จ่าย)เงินไม่ได้"],
    "customer_service": [r"ติดต่อ", r"แอดมิน", r"call ?center", r"คอลเซ็นเตอร์", r"ไม่ตอบ", r"ฝ่ายบริการ", r"เจ้าหน้าที่",
                         r"support", r"แจ้งปัญหา", r"ร้องเรียน"],
    "device_sharing_limits": [r"ครัวเรือน", r"household", r"หลายเครื่อง", r"อุปกรณ์อื่น", r"อีกเครื่อง", r"จำนวนจอ",
                              r"จำกัด(จำนวน)?(เครื่อง|อุปกรณ์)"],
}

CHURN_INTENT = [
    r"ยกเลิก(การ)?(บริการ|สมัคร|สมาชิก|แพ็[คก]|vip)", r"ยกเลิก(ไป)?แล้ว", r"จะยกเลิก", r"คงยกเลิก", r"เลิกใช้", r"เลิกดู", r"เลิกสมัคร", r"ไม่ต่อ", r"ลบแอ[พป]", r"ลบทิ้ง",
    r"ไปดูเถื่อน", r"ย้ายไป", r"ไปใช้(แอ[พป])?(อื่น|เจ้าอื่น)", r"ไม่(ใช้|ดู|สมัคร)อีก", r"จะไม่(ใช้|ดู|สมัคร)",
    r"unsubscribe", r"cancel", r"uninstall", r"อย่าโหลด", r"อย่าสมัคร",
]

LABELS_TH = {
    "paid_no_access": "จ่ายแล้วดูไม่ได้ / VIP ไม่ขึ้น",
    "cancel_difficulty": "ยกเลิกยาก / ยกเลิกแล้วยังโดนหัก",
    "billing_unexpected_charge": "ถูกหักเงิน / ขอเงินคืน",
    "login_account": "เข้าสู่ระบบ / บัญชี",
    "playback_stability": "เล่นวิดีโอไม่เสถียร",
    "ads_intrusive": "โฆษณารบกวน",
    "price_paywall": "ราคา / จ่ายเพิ่ม",
    "content_catalogue": "คอนเทนต์ไม่พอ / หาย",
    "subtitle_dubbing": "ซับไทย / พากย์",
    "tv_device_support": "ใช้บนทีวี",
    "app_update_ux": "อัปเดตแอป / ใช้งานยาก",
    "payment_methods": "ช่องทางจ่ายเงิน",
    "customer_service": "ติดต่อฝ่ายบริการ",
    "device_sharing_limits": "จำกัดอุปกรณ์ / แชร์บัญชี",
}


def compile_lex(patterns):
    return "|".join(f"(?:{p})" for p in patterns)


def tag(df: pd.DataFrame) -> pd.DataFrame:
    text = df["text"].fillna("").str.lower()
    for name, pats in PAINPOINTS.items():
        df[name] = text.str.contains(compile_lex(pats), regex=True, case=False)
    df["churn_intent"] = text.str.contains(compile_lex(CHURN_INTENT), regex=True, case=False)
    df["n_tags"] = df[list(PAINPOINTS)].sum(axis=1)
    df["negative"] = df["rating"] <= 3
    return df


def main():
    df = pd.read_csv(RAW)
    df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
    df = tag(df)
    (ROOT / "data" / "processed").mkdir(parents=True, exist_ok=True)
    df.to_csv(ROOT / "data" / "processed" / "review_tags.csv", index=False, encoding="utf-8-sig")

    neg = df[df.negative]
    cats = list(PAINPOINTS)
    base_churn = neg.churn_intent.mean()
    summary = pd.DataFrame({
        "pain_point": cats,
        "label_th": [LABELS_TH[c] for c in cats],
        "share_of_negative": [neg[c].mean() for c in cats],
        "n_negative": [int(neg[c].sum()) for c in cats],
        "avg_rating_when_mentioned": [df.loc[df[c], "rating"].mean() for c in cats],
        "churn_intent_rate": [neg.loc[neg[c], "churn_intent"].mean() for c in cats],
    })
    summary["churn_intent_lift"] = summary.churn_intent_rate / base_churn
    summary = summary.sort_values("share_of_negative", ascending=False)
    (ROOT / "reports").mkdir(exist_ok=True)
    summary.round(4).to_csv(ROOT / "reports" / "painpoints_summary.csv", index=False)

    by_app = neg.groupby("app")[cats].mean().T.round(3)
    by_app.to_csv(ROOT / "reports" / "painpoints_by_app.csv")

    print(f"Reviews: {len(df):,} | negative (1-3★): {len(neg):,} | "
          f"tagged ≥1: {(neg.n_tags > 0).mean():.0%} | churn intent: {base_churn:.1%}")
    print(summary.round(3).to_string(index=False))
    print("\nShare of negative reviews by app:")
    print(by_app.to_string())


if __name__ == "__main__":
    main()


# ------------------------------------------------------------------ chart
THEMES = {
    "Money & trust\n(paid-no-access, charges,\ncancel, payment methods)":
        ["paid_no_access", "billing_unexpected_charge", "cancel_difficulty", "payment_methods"],
    "Product quality\n(playback, updates, TV)": ["playback_stability", "app_update_ux", "tv_device_support"],
    "Account access\n(login, device limits)": ["login_account", "device_sharing_limits"],
    "Monetisation friction\n(ads, paywall)": ["ads_intrusive", "price_paywall"],
    "Localisation\n(Thai subs / dubbing)": ["subtitle_dubbing"],
    "Customer support": ["customer_service"],
}


def theme_table(df: pd.DataFrame) -> pd.DataFrame:
    neg = df[df.negative]
    base = neg.churn_intent.mean()
    rows = []
    for name, cols in THEMES.items():
        m = neg[cols].any(axis=1)
        rows.append({"theme": name, "share_of_negative": m.mean(), "n": int(m.sum()),
                     "churn_intent_lift": neg.loc[m, "churn_intent"].mean() / base})
    return pd.DataFrame(rows)


def plot_themes(t: pd.DataFrame, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    surface, ink, ink2, muted, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#b9b8b3", "#e7e6e2"
    accent = "#2a78d6"
    t = t.sort_values("share_of_negative")
    colors = [accent if n.startswith("Money") else muted for n in t.theme]

    fig, axes = plt.subplots(1, 2, figsize=(11, 5.2), sharey=True, facecolor=surface,
                             gridspec_kw={"wspace": 0.08})
    panels = [("share_of_negative", "How often it is mentioned\n% of 1–3★ reviews", "{:.0%}"),
              ("churn_intent_lift", "How strongly it signals leaving\nchurn-intent lift vs. average review", "{:.1f}×")]
    for ax, (col, title, fmt) in zip(axes, panels):
        ax.set_facecolor(surface)
        bars = ax.barh(t.theme, t[col], color=colors, height=0.55, edgecolor=surface, linewidth=2)
        for b, v in zip(bars, t[col]):
            ax.text(b.get_width(), b.get_y() + b.get_height() / 2, "  " + fmt.format(v),
                    va="center", ha="left", fontsize=9.5, color=ink)
        ax.set_title(title, loc="left", fontsize=10.5, color=ink2, pad=10)
        ax.tick_params(axis="x", colors=muted, labelsize=8.5, length=0)
        ax.tick_params(axis="y", colors=ink, labelsize=9.5, length=0)
        ax.grid(axis="x", color=grid, linewidth=0.8)
        ax.set_axisbelow(True)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.set_xlim(0, t[col].max() * 1.25)
    axes[0].xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    axes[1].axvline(1, color=ink2, linewidth=1, linestyle=(0, (3, 3)))
    axes[1].text(1.04, -0.62, "average review", color=ink2, fontsize=8.5, va="center")
    axes[1].set_xticks([0, 1, 2, 3])
    axes[1].xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}×"))
    axes[1].set_ylim(-0.85, len(t) - 0.5)

    fig.suptitle("Money & trust problems are as common as playback bugs — and 2.8× more likely to end in churn",
                 x=0.01, ha="left", fontsize=12.5, color=ink, fontweight="bold")
    fig.text(0.01, 0.005, "Source: 4,616 public Thai-language Google Play reviews of 6 streaming apps "
             "(collected 2026-11). Rule-based tagging; a review can carry several themes.",
             fontsize=8, color=ink2, ha="left")
    fig.subplots_adjust(left=0.24, right=0.97, top=0.82, bottom=0.1)
    fig.savefig(path, dpi=200, facecolor=surface)
    plt.close(fig)


def run_chart():
    df = pd.read_csv(ROOT / "data" / "processed" / "review_tags.csv")
    t = theme_table(df)
    t.round(4).to_csv(ROOT / "reports" / "painpoint_themes.csv", index=False)
    (ROOT / "reports" / "figures").mkdir(parents=True, exist_ok=True)
    plot_themes(t, ROOT / "reports" / "figures" / "painpoint_themes.png")
    print(t.round(3).to_string(index=False))


if __name__ == "__main__":
    run_chart()
