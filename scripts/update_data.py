import email.utils
import html
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
OUTPUT = SITE / "data.json"
MAX_FEED_BYTES = 1_500_000
EVENT_WINDOW_DAYS = 45
TRIP_START_DATE = "2026-11-03"
TRIP_END_DATE = "2026-11-09"
TRIP_FOCUS_DATE = "2026-11-05"
TRANSPORT_NOTICE = "Google News RSS search index; not a direct official alert feed"
MONTHS = {
    "janvier": 1, "janv": 1, "jan": 1, "january": 1, "januar": 1,
    "février": 2, "fevrier": 2, "févr": 2, "fevr": 2, "february": 2, "feb": 2,
    "mars": 3, "march": 3, "mar": 3,
    "avril": 4, "avr": 4, "april": 4, "apr": 4,
    "mai": 5, "may": 5,
    "juin": 6, "june": 6, "jun": 6,
    "juillet": 7, "juil": 7, "july": 7, "jul": 7,
    "août": 8, "aout": 8, "august": 8, "aug": 8,
    "septembre": 9, "sept": 9, "september": 9, "sep": 9,
    "octobre": 10, "oct": 10, "october": 10,
    "novembre": 11, "nov": 11, "november": 11,
    "décembre": 12, "decembre": 12, "déc": 12, "dec": 12, "december": 12,
}
FRENCH_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
ENGLISH_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
BASE_FEEDS = [
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
        "query": "",
        "official_domains": [],
    },
    {
        "id": "action_dates",
        "label": "巴黎未来行动日期检索",
        "query": "",
        "official_domains": [],
    },
    {
        "id": "action_routes",
        "label": "游行路线及集合信息",
        "query": "",
        "official_domains": ["prefecturedepolice.interieur.gouv.fr"],
    },
    {
        "id": "action_impacts",
        "label": "交通运营影响信息",
        "query": "",
        "official_domains": ["ratp.fr", "bonjour-ratp.fr", "iledefrance-mobilites.fr", "transilien.com", "sncf-connect.com"],
    },
    {
        "id": "venue_impacts",
        "label": "景点/场馆官方公告检索线索",
        "query": "",
        "official_domains": ["louvre.fr", "musee-orsay.fr", "musee-orangerie.fr", "centrepompidou.fr", "toureiffel.paris", "sainte-chapelle.fr"],
    },
]
COLLECTIVE_CATEGORIES = {"demonstration", "yellow-vest", "strike"}
EVENT_WORDS = re.compile(r"manifestation|rassemblement|mobilisation|gr[eè]ve|strike|protest|demonstration|yellow vest|gilet jaune|blocage|march[eé]", re.I)
ROUTE_WORDS = re.compile(r"itin[eé]raire|parcours|route|procession|d[eé]part|departure|point de rendez-vous", re.I)
TRANSPORT_WORDS = re.compile(r"(?<!\w)(?:ratp|m[eé]tros?|rer|transilien|sncf|stations?|ligne|trafic|transport|train|bus|tram|fermeture|ferm[eé]|closed|closure|perturb\w*)(?!\w)", re.I)
VENUE_WORDS = re.compile(r"mus[eé]e|louvre|orsay|orangerie|pompidou|monument|ch[aâ]teau|tour eiffel|sainte[- ]chapelle|attraction|concert|spectacle|ferm[eé]|fermeture|closed|closure|ouverture|open|access", re.I)
VENUE_NAMES = re.compile(r"louvre|mus[eé]e d.?orsay|orsay|orangerie|pompidou|tour eiffel|eiffel tower|sainte[- ]chapelle|versailles|arc de triomphe|invalides|montmartre", re.I)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_text(value):
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in value if not unicodedata.combining(char)).lower()


def build_feeds(now):
    window_end = now.date() + timedelta(days=EVENT_WINDOW_DAYS)
    months = []
    cursor = date(now.year, now.month, 1)
    while cursor <= window_end:
        months.append((cursor.month, cursor.year))
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    month_terms = " OR ".join(
        f'"{name} {year}"'
        for month, year in months
        for name in (FRENCH_MONTHS[month - 1], ENGLISH_MONTHS[month - 1])
    )
    feeds = [dict(feed) for feed in BASE_FEEDS]
    queries = {
        "news": f'Paris (manifestation OR violences OR emeutes OR greve OR transport) ({month_terms})',
        "action_dates": f'Paris (manifestation OR rassemblement OR mobilisation OR greve OR strike) ({month_terms}) date itineraire parcours Paris',
        "action_routes": f'site:prefecturedepolice.interieur.gouv.fr Paris manifestation (itineraire OR parcours OR rassemblement) ({month_terms}) arrete',
        "action_impacts": f'(site:ratp.fr OR site:bonjour-ratp.fr OR site:iledefrance-mobilites.fr OR site:transilien.com OR site:sncf-connect.com) Paris manifestation fermeture station greve ({month_terms})',
        "venue_impacts": f'(site:louvre.fr OR site:musee-orsay.fr OR site:musee-orangerie.fr OR site:centrepompidou.fr OR site:toureiffel.paris OR site:sainte-chapelle.fr) (fermeture OR fermé OR fermeture exceptionnelle OR closed OR opening OR accès) Paris ({month_terms})',
    }
    for feed in feeds:
        if feed["id"] in queries:
            feed["query"] = queries[feed["id"]]
    return feeds


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
        description = re.sub(r"<[^>]+>", " ", html.unescape(node.findtext("description") or ""))
        description = re.sub(r"\s+", " ", description).strip()[:700]
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
                    "description": description,
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


def month_number(value):
    return next((month for name, month in MONTHS.items() if normalize_text(name) == value), None)


def event_dates(title, published_at, today):
    text = normalize_text(title)
    found = set()

    def add_date(year, month, day):
        try:
            candidate = date(int(year), int(month), int(day))
        except (ValueError, TypeError):
            return
        if today <= candidate <= today + timedelta(days=EVENT_WINDOW_DAYS):
            found.add(candidate.isoformat())

    explicit_dates = []
    for match in re.finditer(r"(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)", text):
        explicit_dates.append(match.span())
        add_date(match.group(1), match.group(2), match.group(3))
    for match in re.finditer(r"(?<!\d)(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?", text):
        explicit_dates.append(match.span())
        add_date(match.group(1), match.group(2), match.group(3))
    for match in re.finditer(r"(?<!\d)(?:(le|du)\s*|(on|for)\s*)?(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})(?!\d)", text):
        explicit_dates.append(match.span())
        french_prefix, english_prefix, first_text, second_text, year_text = match.groups()
        first, second, year = int(first_text), int(second_text), int(year_text)
        if second > 12:
            add_date(year, first, second)
        elif first > 12 or french_prefix:
            add_date(year, second, first)
        elif english_prefix:
            add_date(year, first, second)
    for match in re.finditer(r"(?<!\d)(?:(le|du)\s*|(on|for)\s*)?(\d{1,2})[/.](\d{1,2})(?![/\.\d])", text):
        if any(start <= match.start() and match.end() <= end for start, end in explicit_dates):
            continue
        french_prefix, english_prefix, first_text, second_text = match.groups()
        first, second = int(first_text), int(second_text)
        if first > 12 and second <= 12:
            candidates = [(today.year, second, first), (today.year + 1, second, first)]
        elif first <= 12 and second > 12:
            candidates = [(today.year, first, second), (today.year + 1, first, second)]
        elif first <= 12 and second <= 12 and french_prefix:
            candidates = [(today.year, second, first), (today.year + 1, second, first)]
        elif first <= 12 and second <= 12 and english_prefix:
            candidates = [(today.year, first, second), (today.year + 1, first, second)]
        else:
            candidates = []
        for year, month, day in candidates:
            add_date(year, month, day)

    month_pattern = "|".join(sorted((re.escape(normalize_text(month)) for month in MONTHS), key=len, reverse=True))
    seen_month_days = set()
    for match in re.finditer(rf"(?<!\w)(\d{{1,2}})(?:er|st|nd|rd|th)?\s+({month_pattern})(?:\s+(20\d{{2}}))?", text):
        day, month_name, year = match.groups()
        month = month_number(month_name)
        if month:
            if year:
                add_date(year, month, day)
            else:
                seen_month_days.add((int(month), int(day)))
    for match in re.finditer(rf"(?<!\w)({month_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?", text):
        month_name, day, year = match.groups()
        month = month_number(month_name)
        if month:
            if year:
                add_date(year, month, day)
            else:
                seen_month_days.add((int(month), int(day)))

    for month, day in seen_month_days:
        try:
            current_year_date = date(today.year, month, day)
        except ValueError:
            continue
        if current_year_date >= today:
            add_date(today.year, month, day)
        else:
            add_date(today.year + 1, month, day)
    return sorted(found)


def event_category(title):
    text = normalize_text(title)
    if "yellow vest" in text or "gilet jaune" in text:
        return "yellow-vest", "黄背心相关行动"
    if re.search(r"greve|strike|union|syndicat", text):
        return "strike", "罢工/工会行动"
    if re.search(r"manifestation|rassemblement|mobilisation|protest|demonstration|marche", text):
        return "demonstration", "游行/集会行动"
    return "action", "行动线索"


def event_field(items, pattern, official_only=False):
    matches = []
    for item in items:
        if official_only and not item.get("publisherDomainMatchesOfficial"):
            continue
        combined_text = " ".join((item.get("title", ""), item.get("description", "")))
        if pattern.search(normalize_text(combined_text)):
            matches.append(item)
    if not matches:
        return {"status": "待核实", "value": "标题/摘要中没有匹配到相关细节；请打开原文及当天官方公告核对。", "evidence": []}
    return {
        "status": "官方域名标题/摘要线索，尚未核验原文" if official_only else "新闻索引标题/摘要线索，尚未核验原文",
        "value": "检索标题/摘要关键词线索；本站未打开或核验原文正文，不代表路线、交通或场馆影响已经确认。",
        "evidence": [{"title": item.get("title", ""), "description": item.get("description", ""), "source": item.get("source", ""), "url": item.get("link", ""), "officialDomainMatch": bool(item.get("publisherDomainMatchesOfficial")), "currentFeedSuccess": bool(item.get("_feedOk")), "lastSuccessAt": item.get("_lastSuccessAt", "")} for item in matches[:4]],
    }


def annotate_item(feed, source_item):
    return {
        **source_item,
        "_feedOk": bool(feed.get("ok")),
        "_lastSuccessAt": feed.get("lastSuccessAt") or (feed.get("checkedAt", "") if feed.get("ok") else ""),
    }


def feed_status(feeds, feed_ids):
    return [
        {
            "id": feed.get("id", ""),
            "label": feed.get("label", ""),
            "ok": bool(feed.get("ok")),
            "checkedAt": feed.get("checkedAt", ""),
            "lastSuccessAt": feed.get("lastSuccessAt") or (feed.get("checkedAt", "") if feed.get("ok") else ""),
            "itemCount": len(feed.get("items", [])),
            "error": feed.get("error", ""),
        }
        for feed in feeds
        if feed.get("id") in feed_ids
    ]


def dated_items(feeds, target_date, today, feed_ids):
    matches = []
    for feed in feeds:
        if feed.get("id") not in feed_ids:
            continue
        for source_item in feed.get("items", []):
            item = annotate_item(feed, source_item)
            combined_text = " ".join((item.get("title", ""), item.get("description", "")))
            if target_date in event_dates(combined_text, item.get("publishedAt", ""), today):
                matches.append(item)
    return matches


def no_signal_field(feeds, feed_ids, clear_text, failure_text):
    relevant = [feed for feed in feeds if feed.get("id") in feed_ids]
    failed = [feed for feed in relevant if not feed.get("ok")]
    if failed:
        return {
            "status": "来源刷新失败，无法判断",
            "value": failure_text + "；请打开对应官方公告核对。",
            "evidence": [],
        }
    return {
        "status": "未检索到当天确认线索",
        "value": clear_text,
        "evidence": [],
    }


def dated_field(feeds, target_date, today, feed_ids, pattern, official_only, clear_text, failure_text):
    items = dated_items(feeds, target_date, today, feed_ids)
    field = event_field(items, pattern, official_only=official_only)
    if field.get("evidence"):
        return field
    return no_signal_field(feeds, feed_ids, clear_text, failure_text)


def derive_trip_monitor(feeds, events, generated_at):
    try:
        today = datetime.fromisoformat(generated_at.replace("Z", "+00:00")).astimezone(timezone.utc).date()
    except (AttributeError, ValueError):
        today = datetime.now(timezone.utc).date()

    start = date.fromisoformat(TRIP_START_DATE)
    end = date.fromisoformat(TRIP_END_DATE)
    days = []
    cursor = start
    while cursor <= end:
        target = cursor.isoformat()
        day_events = [event for event in events if event.get("date") == target]
        collective_events = [event for event in day_events if event.get("category") in COLLECTIVE_CATEGORIES]
        if collective_events:
            collective = {
                "status": "有集体游行/罢工日期线索",
                "value": "；".join(event.get("label", "集体行动线索") for event in collective_events),
                "events": [
                    {
                        "id": event.get("id", ""),
                        "category": event.get("category", ""),
                        "label": event.get("label", "集体行动线索"),
                        "status": event.get("status", "待核实"),
                        "sources": event.get("sources", []),
                    }
                    for event in collective_events
                ],
            }
        else:
            action_sources = ["police", "action_dates", "action_routes", "news"]
            source_failed = any(not feed.get("ok") for feed in feeds if feed.get("id") in action_sources)
            collective = {
                "status": "来源刷新失败，无法判断" if source_failed else "暂未检索到集体行动日期线索",
                "value": "行动源本轮有失败，请打开警方及媒体来源核对。" if source_failed else "暂未检索到当天集体游行、罢工或集会的日期线索；不等于当天没有活动。",
                "events": [],
            }

        transport = dated_field(
            feeds,
            target,
            today,
            ["transport", "action_impacts"],
            TRANSPORT_WORDS,
            False,
            "暂未检索到当天交通关闭、改道或运力变化线索；不等于线路一定正常。",
            "交通信息源本轮刷新失败，无法判断当天线路状态",
        )
        venue_official = dated_field(
            feeds,
            target,
            today,
            ["venue_impacts"],
            VENUE_WORDS,
            True,
            "暂未检索到当天官方景点/场馆关闭或营业变化线索；不等于保证开放。",
            "景点/场馆官方信息源本轮刷新失败，无法判断当天开放状态",
        )
        if venue_official.get("evidence"):
            venues = venue_official
        else:
            venues = dated_field(
                feeds,
                target,
                today,
                ["venue_impacts", "action_impacts", "news"],
                VENUE_WORDS,
                False,
                "暂未检索到当天景点/场馆影响线索；不等于保证开放。",
                "景点/场馆相关信息源本轮刷新失败，无法判断当天影响",
            )

        days.append({
            "date": target,
            "focus": target == TRIP_FOCUS_DATE,
            "collective": collective,
            "transport": transport,
            "venues": {**venues, "watchList": "卢浮宫、奥赛博物馆、橘园美术馆、蓬皮杜中心、埃菲尔铁塔、圣礼拜堂、凡尔赛宫、凯旋门、荣军院、蒙马特"},
            "sourceStatus": {
                "collective": feed_status(feeds, ["police", "action_dates", "action_routes", "news"]),
                "transport": feed_status(feeds, ["transport", "action_impacts"]),
                "venues": feed_status(feeds, ["venue_impacts"]),
            },
        })
        cursor += timedelta(days=1)

    return {
        "startDate": TRIP_START_DATE,
        "endDate": TRIP_END_DATE,
        "focusDate": TRIP_FOCUS_DATE,
        "updatedAt": generated_at,
        "priority": "集体游行/罢工优先；每天同时检查交通和景点/场馆影响",
        "days": days,
    }


def derive_events(feeds, generated_at):
    try:
        now = datetime.fromisoformat(generated_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (AttributeError, ValueError):
        now = datetime.now(timezone.utc)
    today = now.date()
    grouped = {}
    for feed in feeds:
        for source_item in feed.get("items", []):
            item = {**source_item, "_feedOk": bool(feed.get("ok")), "_lastSuccessAt": feed.get("lastSuccessAt") or (feed.get("checkedAt", "") if feed.get("ok") else "")}
            title = (item.get("title") or "").strip()
            if not title or not EVENT_WORDS.search(title):
                continue
            if item.get("publishedAt"):
                try:
                    published = datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00")).astimezone(timezone.utc)
                    if published.date() < today - timedelta(days=60):
                        continue
                except ValueError:
                    pass
            category, label = event_category(title)
            for event_date in event_dates(title, item.get("publishedAt", ""), today):
                key = (event_date, category)
                record = grouped.setdefault(key, {"date": event_date, "category": category, "label": label, "items": []})
                fingerprint = normalize_text(title)
                existing_index = next((index for index, existing in enumerate(record["items"]) if normalize_text(existing.get("title", "")) == fingerprint), None)
                if existing_index is None:
                    record["items"].append(item)
                else:
                    existing = record["items"][existing_index]
                    item_rank = (bool(item.get("publisherDomainMatchesOfficial")), bool(item.get("_feedOk")), item.get("publishedAt", ""))
                    existing_rank = (bool(existing.get("publisherDomainMatchesOfficial")), bool(existing.get("_feedOk")), existing.get("publishedAt", ""))
                    if item_rank > existing_rank:
                        record["items"][existing_index] = item
    events = []
    for record in grouped.values():
        items = sorted(record["items"], key=lambda item: (bool(item.get("publisherDomainMatchesOfficial")), item.get("publishedAt", "")), reverse=True)
        official = any(item.get("publisherDomainMatchesOfficial") for item in items)
        events.append({
            "id": f"{record['date']}-{record['category']}",
            "date": record["date"],
            "category": record["category"],
            "label": record["label"],
            "status": "官方域名标题线索；具体内容待核实" if official else "媒体/新闻标题线索；待官方核实",
            "route": event_field(items, ROUTE_WORDS, official_only=True),
            "meetingTime": event_field(items, re.compile(r"\b(?:\d{1,2}[:h]\d{2}|\d{1,2}\s*h(?:\d{2})?)\b|\b(?:heure|horaires|time|at)\b", re.I), official_only=True),
            "districts": event_field(items, re.compile(r"\b(?:paris\s*)?(?:1(?:er|st)|(?:[2-9]|1[0-9]|20)(?:e|eme|th)?)(?:\s*arrondissement)?\b|\barrondissement\s*(?:de\s*)?(?:paris\s*)?(?:1(?:er|st)|[2-9]|1[0-9]|20)\b", re.I), official_only=True),
            "transport": event_field(items, TRANSPORT_WORDS),
            "venues": event_field(items, VENUE_WORDS),
            "venueOfficial": event_field(items, VENUE_NAMES, official_only=True),
            "venueWatchList": "卢浮宫、奥赛博物馆、橘园美术馆、蓬皮杜中心、埃菲尔铁塔、圣礼拜堂、凡尔赛宫、凯旋门、荣军院、蒙马特",
            "sources": [{"title": item.get("title", ""), "source": item.get("source", ""), "url": item.get("link", ""), "publishedAt": item.get("publishedAt", ""), "officialDomainMatch": bool(item.get("publisherDomainMatchesOfficial")), "currentFeedSuccess": bool(item.get("_feedOk")), "lastSuccessAt": item.get("_lastSuccessAt", "")} for item in items[:6]],
        })
    return sorted(events, key=lambda event: (event["date"], event["category"]))


def main():
    previous = load_previous()
    generated_at = now_iso()
    try:
        now = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        now = datetime.now(timezone.utc)
    checked_at = generated_at
    feeds = []
    for config in build_feeds(now):
        try:
            feeds.append(fetch_feed(config))
        except Exception as error:
            result = failed_feed(config, previous, checked_at)
            result["error"] += f"（{type(error).__name__}）"
            feeds.append(result)

    events = derive_events(feeds, generated_at)
    trip_monitor = derive_trip_monitor(feeds, events, generated_at)
    payload = {
        "generatedAt": generated_at,
        "trip": {"startDate": TRIP_START_DATE, "endDate": TRIP_END_DATE, "focusDate": TRIP_FOCUS_DATE},
        "tripMonitor": trip_monitor,
        "refreshAfterSeconds": 900,
        "cacheTtlSeconds": 120,
        "feeds": feeds,
        "events": events,
        "eventWindowDays": EVENT_WINDOW_DAYS,
        "notice": "GitHub Pages 只能读取静态快照；GitHub Actions 计划每 15 分钟更新 data.json。页面打开时可手动刷新或定时读取新快照，关闭页面后不会后台推送。集体游行、罢工和集会优先监控；每天同时检查交通运营与景点/场馆公告。新闻来自 Google News RSS 搜索索引，并非警方或运营商官方实时警报。行动日期为标题线索自动整理；路线、时间、区域、交通及场馆影响未从原文/官方公告确认前均显示待核实。",
        "staticSnapshot": True,
    }
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, OUTPUT)
    print(json.dumps({"generatedAt": payload["generatedAt"], "feedStatus": [{"id": feed["id"], "ok": feed["ok"], "items": len(feed["items"])} for feed in feeds], "events": [{"date": event["date"], "category": event["category"], "sources": len(event["sources"])} for event in events]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
