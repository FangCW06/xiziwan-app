#!/usr/bin/env python3
"""
西子灣海況警示 App — 後端資料抓取程式

依 IMPLEMENTATION_SPEC.md 第2節抓取三筆中央氣象署開放資料,
套用第3節整合邏輯、第4-5節紅黃綠燈判斷邏輯,輸出 xiziwan-status.json。

執行方式:
  CWA_AUTH=your-key python3 fetch_status.py
或於 backend/.env 放 CWA_AUTH=your-key 後直接執行。
"""
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

TW_TZ = timezone(timedelta(hours=8))
API_BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
LOCATION_NAME = "高雄市鼓山區"
COUNTY_NAME = "高雄市"
TIDE_ALTERNATION_WINDOW_MIN = 60  # 潮汐交替時段:滿潮/乾潮前後 1 小時(spec 第4節預設值,待確認)
SUNSET_WARNING_MIN = 120  # 距離日落 < 2 小時(spec 第4-5節定案值)
OPEN_HOUR = 9  # 每日 09:00 開放

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_PATH = os.path.join(SCRIPT_DIR, "..", "xiziwan-status.json")


def load_dotenv(path):
    """極簡 .env 讀取器,不引入外部套件。已存在的環境變數優先,不覆蓋。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def now_tw():
    return datetime.now(TW_TZ)


def http_get_json(url, timeout=20):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} for {url}")
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} for {url}: {e.read().decode('utf-8', 'ignore')}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"連線失敗 {url}: {e.reason}") from e
    return json.loads(body)


def fetch_dataset(dataset_id, params):
    query = urllib.parse.urlencode(params)
    url = f"{API_BASE}/{dataset_id}?{query}"
    return http_get_json(url)


def to_float(raw):
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "None", "-", "NaN"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(raw):
    f = to_float(raw)
    return None if f is None else int(f)


def parse_dt(s):
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# 2.1 潮汐預報 F-A0021-001
# ---------------------------------------------------------------------------
def get_tide_forecast(auth):
    data = fetch_dataset("F-A0021-001", {
        "Authorization": auth,
        "format": "JSON",
        "LocationName": LOCATION_NAME,
    })
    if data.get("success") != "true":
        raise RuntimeError(f"F-A0021-001 回傳非成功狀態: {data}")

    forecasts = data["records"]["TideForecasts"]
    location = forecasts[0]["Location"]
    dailies = location["TimePeriods"]["Daily"]

    events = []  # 攤平所有天的滿潮/乾潮事件,供第4節潮汐交替時段判斷用
    tide_range_by_date = {}
    for daily in dailies:
        tide_range_by_date[daily["Date"]] = daily.get("TideRange")
        for t in daily.get("Time", []):
            events.append({
                "dateTime": parse_dt(t["DateTime"]),
                "tide": t["Tide"],
                "aboveChartDatum": to_int(t["TideHeights"].get("AboveChartDatum")),
            })

    return {"events": events, "tideRangeByDate": tide_range_by_date}


# ---------------------------------------------------------------------------
# 2.2 海況觀測 O-B0075-001(C4P01 / COMC08)
# ---------------------------------------------------------------------------
def get_station_series(auth, station_id):
    data = fetch_dataset("O-B0075-001", {
        "Authorization": auth,
        "format": "JSON",
        "StationID": station_id,
    })
    if data.get("Success") != "true":
        raise RuntimeError(f"O-B0075-001 ({station_id}) 回傳非成功狀態: {data}")

    locations = data["Records"]["SeaSurfaceObs"]["Location"]
    obs_times = locations[0]["StationObsTimes"]["StationObsTime"]

    series = []
    for entry in obs_times:
        series.append((parse_dt(entry["DateTime"]), entry["WeatherElements"]))
    series.sort(key=lambda x: x[0], reverse=True)  # 由新到舊
    return series


def first_valid(series, extractor, cast=to_float):
    """由新到舊掃描 series,回傳第一筆該欄位非缺值的 (值, 該筆時間戳記)。"""
    for dt, elements in series:
        raw = extractor(elements)
        value = cast(raw)
        if value is not None:
            return value, dt
    return None, None


# ---------------------------------------------------------------------------
# 2.3 日出日沒 A-B0062-001
# ---------------------------------------------------------------------------
def get_sun_times(auth, date_str):
    data = fetch_dataset("A-B0062-001", {
        "Authorization": auth,
        "format": "JSON",
        "Date": date_str,
        "CountyName": COUNTY_NAME,
    })
    if data.get("success") != "true":
        raise RuntimeError(f"A-B0062-001 回傳非成功狀態: {data}")

    location = data["records"]["locations"]["location"][0]
    times = location["time"]
    entry = next((t for t in times if t.get("Date") == date_str), times[0])
    return {"sunrise": entry["SunRiseTime"], "sunset": entry["SunSetTime"]}


# ---------------------------------------------------------------------------
# 第4-5節:紅黃綠燈與開放時段判斷邏輯
# ---------------------------------------------------------------------------
def compute_status(now, tide_forecast, c4p01_series, comc08_series, sun_times):
    warnings = []

    tide_height_cm, tide_height_at = first_valid(
        c4p01_series, lambda we: we.get("TideHeight"), to_float)
    if tide_height_cm is not None:
        tide_height_cm = round(tide_height_cm * 100)  # C4P01 回傳單位為公尺,換算為 cm
    tide_level, tide_level_at = first_valid(
        c4p01_series, lambda we: we.get("TideLevel"), cast=lambda v: v if v not in (None, "None", "-", "") else None)
    sea_temp, sea_temp_at = first_valid(
        c4p01_series, lambda we: we.get("SeaTemperature"), to_float)

    wave_height, wave_height_at = first_valid(
        comc08_series, lambda we: we.get("WaveHeight"), to_float)
    wave_period, wave_period_at = first_valid(
        comc08_series, lambda we: we.get("WavePeriod"), to_float)
    wave_direction, wave_direction_at = first_valid(
        comc08_series, lambda we: we.get("WaveDirection"), to_float)
    wind_speed, wind_speed_at = first_valid(
        comc08_series, lambda we: we.get("PrimaryAnemometer", {}).get("WindSpeed"), to_float)
    wind_scale, wind_scale_at = first_valid(
        comc08_series, lambda we: we.get("PrimaryAnemometer", {}).get("WindScale"), to_int)

    if wind_scale is None:
        warnings.append("風級(WindScale)在近 49 筆觀測中皆無有效值,無法用於紅黃燈判斷")
    if wave_height is None:
        warnings.append("浪高(WaveHeight)在近 49 筆觀測中皆無有效值,無法用於紅黃燈判斷")

    today_str = now.strftime("%Y-%m-%d")
    tide_range_today = tide_forecast["tideRangeByDate"].get(today_str)

    # --- 第5節:開放時段與日落倒數 ---
    sunrise_str = sun_times["sunrise"]
    sunset_str = sun_times["sunset"]
    sunset_hour, sunset_min = (int(x) for x in sunset_str.split(":"))
    sunset_dt = now.replace(hour=sunset_hour, minute=sunset_min, second=0, microsecond=0)
    open_dt = now.replace(hour=OPEN_HOUR, minute=0, second=0, microsecond=0)

    minutes_until_sunset = round((sunset_dt - now).total_seconds() / 60)

    if now < open_dt:
        is_open_now = False
        openness = "beforeOpen"
    elif now > sunset_dt:
        is_open_now = False
        openness = "afterClose"
    else:
        is_open_now = True
        openness = "open"

    # --- 潮汐交替時段判斷(滿潮/乾潮前後 60 分鐘)---
    in_tide_alternation = False
    for event in tide_forecast["events"]:
        delta_min = abs((now - event["dateTime"]).total_seconds() / 60)
        if delta_min <= TIDE_ALTERNATION_WINDOW_MIN:
            in_tide_alternation = True
            break

    # --- 第4節:紅黃綠燈判斷(任一硬指標觸發即降級,不做加權平均)---
    if (wind_scale is not None and wind_scale >= 6) or \
       (wave_height is not None and wave_height > 3) or \
       (not is_open_now):
        alert_level = "red"
    elif (wind_scale is not None and 4 <= wind_scale <= 5) or \
         (wave_height is not None and 1 <= wave_height <= 3) or \
         in_tide_alternation or \
         (is_open_now and minutes_until_sunset < SUNSET_WARNING_MIN):
        alert_level = "yellow"
    else:
        alert_level = "green"

    status = {
        "location": "西子灣",
        "lastFetchedAt": now.isoformat(),
        "tide": {
            "height": tide_height_cm,
            "level": tide_level,
            "tideRange": tide_range_today,
            "observedAt": tide_height_at.isoformat() if tide_height_at else None,
            "source": "C4P01 / F-A0021-001",
        },
        "wave": {
            "height": wave_height,
            "period": wave_period,
            "direction": wave_direction,
            "observedAt": wave_height_at.isoformat() if wave_height_at else None,
            "source": "COMC08",
        },
        "wind": {
            "speed": wind_speed,
            "scale": wind_scale,
            "observedAt": wind_scale_at.isoformat() if wind_scale_at else None,
            "source": "COMC08",
        },
        "seaTemperature": {
            "value": sea_temp,
            "observedAt": sea_temp_at.isoformat() if sea_temp_at else None,
            "source": "C4P01",
        },
        "sun": {
            "sunrise": sunrise_str,
            "sunset": sunset_str,
        },
        "alertLevel": alert_level,
        "isOpenNow": is_open_now,
        "openness": openness,
        "minutesUntilSunset": minutes_until_sunset,
        "inTideAlternationWindow": in_tide_alternation,
        "dataWarnings": warnings,
    }
    return status


def main():
    load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
    auth = os.environ.get("CWA_AUTH")
    if not auth:
        print("錯誤:找不到 CWA_AUTH 環境變數(可設環境變數,或於 backend/.env 提供)", file=sys.stderr)
        sys.exit(1)

    output_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT_PATH

    now = now_tw()
    today_str = now.strftime("%Y-%m-%d")

    print("抓取潮汐預報 F-A0021-001 ...")
    tide_forecast = get_tide_forecast(auth)

    print("抓取海況觀測 O-B0075-001 (C4P01) ...")
    c4p01_series = get_station_series(auth, "C4P01")

    print("抓取海況觀測 O-B0075-001 (COMC08) ...")
    comc08_series = get_station_series(auth, "COMC08")

    print("抓取日出日沒 A-B0062-001 ...")
    sun_times = get_sun_times(auth, today_str)

    status = compute_status(now, tide_forecast, c4p01_series, comc08_series, sun_times)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    print(f"完成,已寫入 {os.path.abspath(output_path)}")
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
