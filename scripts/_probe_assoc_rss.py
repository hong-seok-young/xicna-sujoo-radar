"""4개 협회/전문지 RSS — UA 우회 + 여러 URL 패턴 시도."""
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (compatible; Feedfetcher-Google; +http://www.google.com/feedfetcher.html)",
    "sujoo-radar/0.1",
]

# (매체명, [후보 URL])
CANDIDATES = [
    ("KCCA 한국클린룸설비협회", [
        "https://www.kooa.or.kr/rss",
        "https://www.kooa.or.kr/rss.xml",
        "https://www.kooa.or.kr/rss/allArticle.xml",
        "https://www.kooa.or.kr/feed",
        "https://kooa.or.kr/rss",
    ]),
    ("SEIA 반도체설비산업협회", [
        "https://www.seia.or.kr/rss",
        "https://www.seia.or.kr/rss.xml",
        "https://seia.or.kr/rss",
        "https://www.seia.or.kr/feed",
    ]),
    ("히트뉴스", [
        "https://www.hitnews.co.kr/rss/allArticle.xml",
        "https://www.hitnews.co.kr/rss/S1N1.xml",
        "https://www.hitnews.co.kr/rss",
        "http://www.hitnews.co.kr/rss/allArticle.xml",
    ]),
    ("식품음료신문", [
        "https://www.thinkfood.co.kr/rss/allArticle.xml",
        "https://www.thinkfood.co.kr/rss/S1N1.xml",
        "https://www.thinkfood.co.kr/rss",
        "http://www.thinkfood.co.kr/rss/allArticle.xml",
    ]),
]

for name, urls in CANDIDATES:
    print(f"\n=== {name} ===")
    for url in urls:
        for ua in UA_LIST:
            try:
                r = requests.get(url, headers={"User-Agent": ua}, timeout=10, verify=False)
                ct = r.headers.get("content-type", "")[:40]
                preview = r.text[:120].replace("\n", " ")
                marker = "✓" if r.status_code == 200 and ("<rss" in r.text or "<feed" in r.text or "<channel" in r.text) else " "
                print(f"  {marker} [{r.status_code:>3}] {ct:40s} | UA={ua[:30]:30s} | {url}")
                if marker == "✓":
                    break
            except Exception as e:
                print(f"    [ERR] {type(e).__name__}: {str(e)[:60]} | {url}")
        else:
            continue
        break
