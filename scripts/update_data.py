import email.utils
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
OUTPUT = SITE / "data.json"
MAX_FEED_BYTES = 1_500_000
FEEDS = [
    {
        "id": "police",
        "label": "警方公告检索线索",
        "query": "site:prefecturedepolice.interieur.gouv.fr Paris (manifestation OR circulation OR interdiction OR arrêté OR rassemblement)",
        "official_domains": ["prefecturedepolice.interieur.gouv.fr"],
    },
    {
        "id": "transport",
        "label": "交通运营商检索线索",
        "query": "(site:ratp.fr OR site:bonjour-ratp.fr OR site:iledefrance-mobilites.fr OR site:transilien.com OR site:sncf-connect.com) (trafic OR grève OR fermeture OR perturbation) Paris",
        "official_domains": ["ratp.fr", "bonjour-ratp.fr", "iledefrance-mobilites.fr", "transilien.com", "sncf-connect.com"],
    },
    {
        "id": "news",
        "label": "媒体报道检索",
        "query": "Paris (manifestation OR violences OR émeutes OR grève OR transport) octobre novembre 2026",
        "official_domains": [],
    },
]
TRANSPORT_NOTICE = "Google News RSS search index; not a direct official alert feed"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_http_url(value):
    parsed = urllib.parse.urlparse((value or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return parsed.geturl()


def domain_matches(host, allowed):
    host = (host or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in allowed)


def fetch_feed(config):
    query = urllib.parse.quote(config["query"], safe="")
    url = f"https://news.google.com/rss/search?q={query}&hl=fr&gl=FR&ceid=FR%3Afr"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ParisTravelWatch/1.0 (+travel-safety information dashboard)",
            "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read(MAX_FEED_BYTES + 1)
    if len(body) > MAX_FEED_BYTES:
        raise ValueError("Feed exceeded the response size limit")

    root = ET.fromstring(body)
    if root.find("./channel") is None:
        raise ValueError("Feed did not contain an RSS channel")

    items = []
    for node in root.findall("./channel/item")[:12]:
        title = (node.findtext("title") or "").strip()
        link = safe_http_url(node.findtext("link") or "")
        source_node = node.find("source")
        source = (source_node.text or "Google News") if source_node is not None else "Google News"
        source_url = safe_http_url(source_node.get("url", "")) if source_node is not None else ""
        source_host = urllib.parse.urlparse(source_url).hostname or ""
        try:
            parsed_date = email.utils.parsedate_to_datetime((node.findtext("pubDate") or "").strip())
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            published_at = parsed_date.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError):
            published_at = ""
        if title and link:
            items.append(
                {
                    "id": (node.findtext("guid") or link).strip(),
                    "title": title,
                    "link": link,
                    "source": source.strip(),
                    "sourceHost": source_host,
                    "publisherUrl": source_url,
                    "publishedAt": published_at,
                    "publisherDomainMatchesOfficial": domain_matches(source_host, config["official_domains"]),
                }
            )

    return {
        "id": config["id"],
        "label": config["label"],
        "items": items,
        "ok": True,
        "checkedAt": now_iso(),
        "error": "",
        "transport": TRANSPORT_NOTICE,
    }


def load_previous():
    try:
        return json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"feeds": []}


def failed_feed(config, previous, checked_at):
    prior = next((feed for feed in previous.get("feeds", []) if feed.get("id") == config["id"]), {})
    last_success = prior.get("lastSuccessAt") or (prior.get("checkedAt", "") if prior.get("ok") else "")
    return {
        "id": config["id"],
        "label": config["label"],
        "items": prior.get("items", []),
        "ok": False,
        "checkedAt": checked_at,
        "error": "此检索源本次刷新失败。" + ("列表保留最近一次成功结果，请查看其时间戳。" if prior.get("items") else "请直接打开官方来源核对。"),
        "lastSuccessAt": last_success,
        "transport": TRANSPORT_NOTICE,
    }


def main():
    previous = load_previous()
    checked_at = now_iso()
    feeds = []
    for config in FEEDS:
        try:
            feeds.append(fetch_feed(config))
        except Exception as error:
            result = failed_feed(config, previous, checked_at)
            result["error"] += f"（{type(error).__name__}）"
            feeds.append(result)

    payload = {
        "generatedAt": now_iso(),
        "refreshAfterSeconds": 1800,
        "cacheTtlSeconds": 120,
        "feeds": feeds,
        "notice": "GitHub Pages 只能读取静态快照；GitHub Actions 计划每 30 分钟更新 data.json。页面打开时可手动刷新或定时读取新快照，关闭页面后不会后台推送。新闻内容来自 Google News RSS 搜索索引，不是官方实时警报。",
        "staticSnapshot": True,
    }
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, OUTPUT)
    print(json.dumps({"generatedAt": payload["generatedAt"], "feeds": [{"id": feed["id"], "ok": feed["ok"], "items": len(feed["items"]), "lastSuccessAt": feed.get("lastSuccessAt", "")} for feed in feeds]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
