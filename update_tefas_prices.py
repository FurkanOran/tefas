"""TEFAS fiyat geçmişini GitHub Pages için statik JSON'a dönüştürür."""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://www.tefas.gov.tr/api/funds/fonGnlBlgSiraliGetir"
FVT_CHART_URL = "https://fvt.com.tr/api/funds/{code}/chart"
FUND_CODES = ("TLY", "THF", "TMV", "DOH")
MAX_DAYS_PER_REQUEST = 28
INITIAL_HISTORY_DAYS = 365
REFRESH_LOOKBACK_DAYS = 35
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "price-history.json"


def read_snapshot() -> dict:
    if not OUTPUT_PATH.exists():
        return {"funds": {}}
    try:
        return json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"funds": {}}


def parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def request_tefas_rows(code: str, start: date, end: date) -> list[dict]:
    payload = {
        "fonTipi": "YAT", "fonKodu": code, "aramaMetni": None,
        "fonTurKod": None, "fonGrubu": None, "sfonTurKod": None,
        "fonTurAciklama": None, "kurucuKod": None,
        "basTarih": start.strftime("%Y%m%d"), "bitTarih": end.strftime("%Y%m%d"),
        "basSira": 1, "bitSira": 100000, "dil": "TR", "sFonTurKod": "",
        "fonKod": "", "fonGrup": "", "fonUnvanTip": "",
    }
    request = Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=40) as response:
        body = json.loads(response.read().decode("utf-8"))
    if body.get("errorCode") or body.get("errorMessage"):
        raise RuntimeError(body.get("errorMessage") or body.get("errorCode"))
    return body.get("resultList") or []


def request_fvt_rows(code: str, start: date, end: date) -> list[dict]:
    query = urlencode({"baslangic": start.isoformat(), "bitis": end.isoformat()})
    request = Request(
        f"{FVT_CHART_URL.format(code=code)}?{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urlopen(request, timeout=40) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not body.get("success"):
        raise RuntimeError("FVT fiyat verisi alınamadı")
    return body.get("data") or []


def request_rows(code: str, start: date, end: date) -> tuple[list[dict], str]:
    try:
        return request_tefas_rows(code, start, end), "TEFAS"
    except Exception as tefas_error:
        print(f"{code} · TEFAS erişilemedi ({tefas_error}); FVT yedeği deneniyor.")
        return request_fvt_rows(code, start, end), "FVT"


def collect(code: str, existing: list[dict], today: date) -> tuple[list[dict], set[str]]:
    old = {str(row.get("time"))[:10]: row for row in existing if parse_date(row.get("time"))}
    known_dates = [parse_date(key) for key in old]
    latest = max((item for item in known_dates if item), default=None)
    start = max(today - timedelta(days=REFRESH_LOOKBACK_DAYS), latest - timedelta(days=5)) if latest else today - timedelta(days=INITIAL_HISTORY_DAYS)
    cursor = start
    sources = set()
    while cursor <= today:
        chunk_end = min(today, cursor + timedelta(days=MAX_DAYS_PER_REQUEST - 1))
        rows, source = request_rows(code, cursor, chunk_end)
        sources.add(source)
        for row in rows:
            row_date = parse_date(row.get("tarih"))
            try:
                value = float(row.get("fiyat"))
            except (TypeError, ValueError):
                continue
            if row_date and value > 0:
                old[row_date.isoformat()] = {"time": row_date.isoformat(), "value": value}
        cursor = chunk_end + timedelta(days=1)
        if cursor <= today:
            time.sleep(0.2)
    return [old[key] for key in sorted(old)], sources


def main() -> None:
    snapshot = read_snapshot()
    funds = snapshot.get("funds") if isinstance(snapshot.get("funds"), dict) else {}
    today = date.today()
    result = {}
    used_sources = set()
    for code in FUND_CODES:
        result[code], sources = collect(code, funds.get(code, []), today)
        used_sources.update(sources)
        print(f"{code}: {len(result[code])} fiyat kaydı")
        time.sleep(0.2)
    payload = {
        "source": " / ".join(sorted(used_sources)) or "TEFAS",
        "updatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "funds": result,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
