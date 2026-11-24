"""Market Pulse: pull US market quotes and news from Finnhub, write JSON for the
dashboard, and post a daily brief to Discord and/or Slack via webhooks.

Runs in GitHub Actions on a schedule. Standard library only, no installs needed.

Environment variables (set as GitHub repository secrets):
  FINNHUB_API_KEY      required for live data (free key at finnhub.io)
  DISCORD_WEBHOOK_URL  optional, posts the brief to a Discord channel
  SLACK_WEBHOOK_URL    optional, posts the brief to a Slack channel
  ANTHROPIC_API_KEY    optional, has Claude write a 3-bullet summary of the day
  ANTHROPIC_MODEL      optional, defaults to claude-sonnet-5-5

Run locally with sample data:  python market_pulse.py --sample
"""
import json, os, re, sys, time, random, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

INDICES = [("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("DIA", "Dow 30"), ("IWM", "Russell 2000")]
SECTORS = [("XLK", "Technology"), ("XLC", "Communication"), ("XLY", "Consumer Disc."), ("XLF", "Financials"),
           ("XLV", "Health Care"), ("XLI", "Industrials"), ("XLE", "Energy"), ("XLP", "Consumer Staples"),
           ("XLU", "Utilities"), ("XLB", "Materials"), ("XLRE", "Real Estate")]
WATCHLIST = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "WMT", "UPS"]

POS = r"\b(beat|beats|surge|surges|rally|rallies|record|gain|gains|jump|jumps|soar|soars|rise|rises|upgrade|upgraded|raise|raises|strong|boost|tops)\b"
NEG = r"\b(miss|misses|fall|falls|drop|drops|plunge|plunges|slump|slumps|cut|cuts|downgrade|downgraded|lawsuit|probe|recession|tumble|tumbles|slide|slides|warn|warns|weak|layoffs|sink|sinks)\b"
TOPICS = {"Fed and rates": r"\b(fed|powell|rate|rates|yield|yields|treasury|inflation|cpi)\b",
          "Earnings": r"\b(earnings|revenue|profit|quarter|guidance|eps)\b",
          "AI": r"\b(ai|artificial intelligence|chip|chips|gpu|nvidia|data center)\b",
          "Energy": r"\b(oil|crude|opec|gas|energy)\b",
          "Trade": r"\b(tariff|tariffs|trade|export|import|china)\b",
          "Deals": r"\b(merger|acquire|acquires|acquisition|deal|buyout|ipo)\b"}


def http_json(url, payload=None, headers=None, timeout=20):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "market-pulse/1.0", **(headers or {})})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
        return json.loads(body) if body.strip() else {}


def finnhub(path, **params):
    params["token"] = os.environ["FINNHUB_API_KEY"]
    return http_json("https://finnhub.io/api/v1/" + path + "?" + urllib.parse.urlencode(params))


def quote(symbol):
    q = finnhub("quote", symbol=symbol)
    time.sleep(0.25)  # stay well under the free tier's per-minute limit
    if not q or not q.get("c"):
        raise ValueError(f"No quote returned for {symbol}")
    return {"price": round(q["c"], 2), "change": round(q.get("d") or 0, 2), "pct": round(q.get("dp") or 0, 2),
            "open": q.get("o"), "high": q.get("h"), "low": q.get("l"), "prev_close": q.get("pc")}


def tag_headline(text):
    t = text.lower().replace("rate cut", "rate-easing")  # a rate cut is not bad news
    pos, neg = len(re.findall(POS, t)), len(re.findall(NEG, t))
    tone = "positive" if pos > neg else "negative" if neg > pos else "neutral"
    return tone, [name for name, rx in TOPICS.items() if re.search(rx, t)]


def build_live():
    out = {"sample": False, "indices": [], "sectors": [], "watchlist": [], "news": [], "errors": []}
    for group, items in (("indices", INDICES), ("sectors", SECTORS), ("watchlist", [(s, s) for s in WATCHLIST])):
        for sym, name in items:
            try:
                out[group].append({"symbol": sym, "name": name, **quote(sym)})
            except Exception as e:  # keep going, record what failed
                out["errors"].append(f"{sym}: {e}")
    try:
        for n in finnhub("news", category="general")[:25]:
            tone, topics = tag_headline(n.get("headline", "") + " " + n.get("summary", ""))
            out["news"].append({"headline": n.get("headline"), "source": n.get("source"), "url": n.get("url"),
                                "time": datetime.fromtimestamp(n.get("datetime", 0), timezone.utc).isoformat(),
                                "related": [s for s in (n.get("related") or "").split(",") if s],
                                "tone": tone, "topics": topics})
    except Exception as e:
        out["errors"].append(f"news: {e}")
    return out


def build_sample():
    """Invented numbers and headlines so the pipeline can be tested without a key."""
    rnd = random.Random(42)
    def q(base, vol):
        pct = round(rnd.gauss(0.2, vol), 2); price = round(base * (1 + pct / 100), 2)
        return {"price": price, "change": round(price - base, 2), "pct": pct, "prev_close": base}
    bases = {"SPY": 612.4, "QQQ": 548.9, "DIA": 452.1, "IWM": 238.7, "AAPL": 248.3, "MSFT": 512.6, "NVDA": 186.4,
             "AMZN": 231.2, "GOOGL": 205.8, "META": 702.5, "TSLA": 342.9, "JPM": 291.4, "WMT": 104.7, "UPS": 98.6}
    out = {"sample": True, "errors": [],
           "indices": [{"symbol": s, "name": n, **q(bases[s], 0.8)} for s, n in INDICES],
           "sectors": [{"symbol": s, "name": n, **q(100, 1.1)} for s, n in SECTORS],
           "watchlist": [{"symbol": s, "name": s, **q(bases[s], 2.0)} for s in WATCHLIST], "news": []}
    heads = ["Sample: Chipmakers rally as AI data center spending outlook rises",
             "Sample: Treasury yields slide after softer inflation reading",
             "Sample: Retailer tops quarterly earnings estimates, raises guidance",
             "Sample: Oil prices fall as supply concerns ease",
             "Sample: Regional bank shares drop after loan loss warning",
             "Sample: Parcel carrier announces network changes to cut costs",
             "Sample: Tech giant agrees to acquire cybersecurity startup",
             "Sample: New tariff proposal weighs on industrial stocks"]
    now = datetime.now(timezone.utc)
    for i, h in enumerate(heads):
        tone, topics = tag_headline(h)
        out["news"].append({"headline": h, "source": "Sample feed", "url": "", "time": (now - timedelta(minutes=37 * i + 5)).isoformat(),
                            "related": [], "tone": tone, "topics": topics})
    return out


def rule_brief(d):
    idx = ", ".join(f"{i['name']} {i['pct']:+.2f}%" for i in d["indices"])
    lines = [f"Markets: {idx}."]
    if d["sectors"]:
        s = sorted(d["sectors"], key=lambda x: x["pct"])
        lines.append(f"Best sector: {s[-1]['name']} {s[-1]['pct']:+.2f}%. Worst: {s[0]['name']} {s[0]['pct']:+.2f}%.")
    if d["watchlist"]:
        w = max(d["watchlist"], key=lambda x: abs(x["pct"]))
        lines.append(f"Biggest watchlist move: {w['symbol']} {w['pct']:+.2f}%.")
    tones = [n["tone"] for n in d["news"]]
    if tones:
        lines.append(f"Headline tone: {tones.count('positive')} positive, {tones.count('negative')} negative, {tones.count('neutral')} neutral.")
    return " ".join(lines)


def claude_brief(d):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or d.get("sample"):
        return None
    facts = {"indices": d["indices"], "sectors": d["sectors"], "watchlist": d["watchlist"],
             "headlines": [n["headline"] for n in d["news"][:15]]}
    prompt = ("Write exactly 3 short bullet points summarizing today's US market session for a busy professional. "
              "Use only the data provided. No advice, no predictions.\n\n" + json.dumps(facts))
    try:
        r = http_json("https://api.anthropic.com/v1/messages",
                      {"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "max_tokens": 300,
                       "messages": [{"role": "user", "content": prompt}]},
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in r.get("content", [])).strip() or None
    except Exception as e:
        d["errors"].append(f"claude: {e}")
        return None


def post_webhooks(d, site_url):
    text = d["brief"] + (f"\n\nAI summary:\n{d['ai_summary']}" if d.get("ai_summary") else "")
    sent = []
    discord = os.environ.get("DISCORD_WEBHOOK_URL")
    if discord:
        spy = next((i for i in d["indices"] if i["symbol"] == "SPY"), None)
        color = 0x4FD6A8 if spy and spy["pct"] >= 0 else 0xFF6B6B
        try:
            http_json(discord, {"username": "Market Pulse", "embeds": [{"title": "Market Pulse daily brief", "description": text[:4000],
                                 "color": color, "url": site_url or None, "timestamp": d["generated_at"]}]})
            sent.append("discord")
        except Exception as e:
            d["errors"].append(f"discord: {e}")
    slack = os.environ.get("SLACK_WEBHOOK_URL")
    if slack:
        try:
            http_json(slack, {"text": f"*Market Pulse daily brief*\n{text}" + (f"\n<{site_url}|Open the dashboard>" if site_url else "")})
            sent.append("slack")
        except Exception as e:
            d["errors"].append(f"slack: {e}")
    return sent


def update_history(d):
    path = os.path.join(DATA, "history.json")
    try:
        hist = json.load(open(path))
    except Exception:
        hist = []
    if d.get("sample") and not hist:
        rnd, base = random.Random(7), {i["symbol"]: i["prev_close"] for i in d["indices"]}
        today = datetime.now(timezone.utc).date()
        days = [today - timedelta(days=k) for k in range(45, 0, -1) if (today - timedelta(days=k)).weekday() < 5]
        level = {s: v * 0.96 for s, v in base.items()}
        for day in days:
            level = {s: round(v * (1 + rnd.gauss(0.0012, 0.008)), 2) for s, v in level.items()}
            hist.append({"date": day.isoformat(), **level})
    row = {"date": d["generated_at"][:10], **{i["symbol"]: i["price"] for i in d["indices"]}}
    hist = [h for h in hist if h["date"] != row["date"]] + [row]
    hist = hist[-60:]
    json.dump(hist, open(path, "w"), indent=1)


def main():
    sample = "--sample" in sys.argv or not os.environ.get("FINNHUB_API_KEY")
    d = build_sample() if sample else build_live()
    d["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    d["brief"] = rule_brief(d)
    d["ai_summary"] = claude_brief(d)
    site = os.environ.get("SITE_URL", "")
    d["webhooks_sent"] = [] if sample else post_webhooks(d, site)
    os.makedirs(DATA, exist_ok=True)
    json.dump(d, open(os.path.join(DATA, "market.json"), "w"), indent=1)
    update_history(d)
    print(d["brief"])
    if d["errors"]:
        print("Warnings:", *d["errors"], sep="\n  ")
    if not sample and not d["indices"]:
        sys.exit("No index data came back. Check the FINNHUB_API_KEY secret.")


if __name__ == "__main__":
    main()
