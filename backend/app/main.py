import json, os
import threading, time
import urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from .models import PlantConfig, default_config
from .weather import CSVWeatherProvider
from .engine import power, energy
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("SOLAR_CONFIG_PATH", ROOT / "config" / "plant.json"))
CSV_PATH = Path(os.getenv("SOLAR_WEATHER_CSV", ROOT / "SOLAR_WEATHER_CSV.csv"))
app = FastAPI(title="Solar PV Monitoring & Generation Forecasting", version="1.0.0")
frontend_origins = [origin.strip() for origin in os.getenv("SOLAR_FRONTEND_ORIGINS", "").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", *frontend_origins], allow_methods=["*"], allow_headers=["*"])
provider = CSVWeatherProvider(str(CSV_PATH))
LIVE_WEATHER_CACHE_SECONDS = 600
live_weather_cache = None
live_weather_cache_at = 0.0
live_weather_lock = threading.Lock()

def load_config():
    if CONFIG_PATH.exists():
        try: return PlantConfig.model_validate_json(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception: pass
    return default_config()

def save_config(c):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(c.model_dump_json(indent=2), encoding="utf-8")

def current_provider(c=None):
    c = c or load_config()
    return CSVWeatherProvider(str(CSV_PATH), c.timezone)

@app.get("/api/health")
def health(): return {"status": "ok"}

@app.get("/api/config")
def get_config():
    c=load_config(); return {**c.model_dump(), "capacity_kw": c.capacity_kw}

@app.put("/api/config")
def put_config(config: PlantConfig):
    save_config(config)
    return {**config.model_dump(), "capacity_kw": config.capacity_kw}

@app.get("/api/weather/status")
def weather_status(): return current_provider().coverage()

def fetch_karad_weather():
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        raise ValueError("OpenWeather API key is missing. Set OPENWEATHER_API_KEY in the backend environment.")
    query=urllib.parse.urlencode({"lat":17.28937,"lon":74.18183,"appid":api_key,"units":"metric"})
    request=urllib.request.Request(f"https://api.openweathermap.org/data/2.5/weather?{query}",headers={"User-Agent":"SolarPVMonitor/1.0"})
    with urllib.request.urlopen(request,timeout=8) as response:
        payload=json.loads(response.read().decode("utf-8"))
    current=payload.get("main",{})
    temperature=current.get("temp")
    if temperature is None: raise ValueError("Live temperature is missing from the weather response")
    observed_at=payload.get("dt")
    timestamp=datetime.fromtimestamp(observed_at,timezone.utc).isoformat() if observed_at else None
    return {"available":True,"city":"Karad","temperature_c":float(temperature),"timestamp":timestamp,"source":"OpenWeather current conditions"}

@app.get("/api/weather/karad-current")
def karad_current_weather():
    global live_weather_cache, live_weather_cache_at
    with live_weather_lock:
        if live_weather_cache and time.monotonic() - live_weather_cache_at < LIVE_WEATHER_CACHE_SECONDS:
            return live_weather_cache
        try:
            live_weather_cache = fetch_karad_weather()
            live_weather_cache_at = time.monotonic()
            return live_weather_cache
        except Exception as exc:
            if live_weather_cache:
                return {**live_weather_cache, "stale": True}
            raise HTTPException(status_code=502,detail=f"Karad live temperature is unavailable: {exc}")

@app.post("/api/weather/upload")
async def upload_weather(file: UploadFile = File(...)):
    global CSV_PATH
    if not file.filename or Path(file.filename).suffix.lower() != ".csv":
        raise HTTPException(status_code=400, detail="Choose a .csv weather file.")
    upload_dir = ROOT / "data" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name
    destination = upload_dir / safe_name
    temp_path = destination.with_suffix(destination.suffix + ".uploading")
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="The selected CSV file is empty.")
        temp_path.write_bytes(content)
        candidate = CSVWeatherProvider(str(temp_path), load_config().timezone)
        coverage = candidate.coverage()
        if not coverage.get("available"):
            raise HTTPException(status_code=400, detail=coverage.get("error", "CSV validation failed."))
        temp_path.replace(destination)
        CSV_PATH = destination
        return {"message": "Weather CSV uploaded and validated.", "status": current_provider().coverage()}
    except HTTPException:
        temp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Could not save weather CSV: {exc}")
    finally:
        await file.close()

def find_current(c, df, provider):
    tz=ZoneInfo(c.timezone); now=datetime.now(tz)
    try: target=now.replace(year=now.year-1)
    except ValueError: target=now.replace(year=now.year-1, day=28)
    rec=provider.record_at(target)
    if rec: return rec, "previous_year_exact"
    rec=provider.nearest_same_date(target)
    if rec: return rec, "previous_year_nearest_same_date"
    ref=datetime.fromisoformat(c.reference_date).replace(tzinfo=tz)
    rec=provider.nearest_same_date(ref)
    if rec: return rec, "reference_date"
    return None, "unavailable"

def calc_at(c, rec):
    active=[s for s in c.subsystems if s.active]
    parts=[{"id":s.id,"name":s.name,"power_kw":power(s,rec,c.calculation_model),"capacity_kw":s.ac_capacity_kw} for s in active]
    return parts, sum(x["power_kw"] for x in parts)

@app.get("/api/dashboard")
def dashboard():
    c=load_config(); provider=current_provider(c); df=provider.ensure()
    if df is None or df.empty:
        return {"available":False,"error":provider.error,"config":{"plant_name":c.plant_name,"capacity_kw":c.capacity_kw,"subsystem_count":sum(s.active for s in c.subsystems)},"current":None,"forecast":None,"series":[]}
    current, source=find_current(c,df,provider)
    if current is None:
        return {"available":False,"error":"No data for corresponding previous-year date or configured reference date.","config":{"plant_name":c.plant_name,"capacity_kw":c.capacity_kw,"subsystem_count":sum(s.active for s in c.subsystems)},"current":None,"forecast":None,"series":[]}
    parts,total=calc_at(c,current)
    nxt=provider.next_record(current["period_end"])
    forecast=None
    if nxt:
        fparts,ftotal=calc_at(c,nxt)
        seconds=(nxt["period_end"]-current["period_end"]).total_seconds()
        forecast={"timestamp":nxt["period_end"].isoformat(),"gti":float(nxt["gti"]),"air_temp":float(nxt["air_temp"]),"subsystems":fparts,"power_kw":ftotal,"interval_seconds":seconds,"energy_kwh":energy(ftotal,seconds),"approximation":"next timestamp power held constant over interval"}
    day_stamps=df[df.period_end.dt.date == current["period_end"].date()]
    # Dashboard trend lines end at the selected operating timestamp; later rows
    # belong to the forecast horizon and must not appear as observed history.
    stamps=day_stamps[day_stamps.period_end <= current["period_end"]]
    daily=0.0
    rows=list(day_stamps.iterrows())
    for i,(idx,row) in enumerate(rows):
        sec=(rows[i+1][1].period_end-row.period_end).total_seconds() if i+1<len(rows) else (df.period_end.diff().median().total_seconds() if len(df)>1 else 0)
        _,p=calc_at(c,row)
        daily+=energy(p,sec)
    out=[]
    for _,row in stamps.iterrows():
        wp=provider.json_record(row); sub,combined=calc_at(c,row)
        out.append({"timestamp":wp["period_end"],"subsystems":sub,"combined_kw":combined})
    return {"available":True,"selection":source,"config":{"plant_name":c.plant_name,"capacity_kw":c.capacity_kw,"subsystem_count":sum(s.active for s in c.subsystems),"calculation_model":c.calculation_model},
        "current":{"timestamp":current["period_end"].isoformat(),"gti":float(current["gti"]),"air_temp":float(current["air_temp"]),"subsystems":parts,"power_kw":total,"daily_energy_kwh":daily,"energy_period":"partial day"},
        "forecast":forecast,"series":out}

@app.get("/api/forecast")
def forecast():
    c=load_config(); provider=current_provider(c); df=provider.ensure()
    if df is None or df.empty:
        return {"available":False,"error":provider.error,"current":None,"historical":[],"forecast":[],"subsystems":[]}
    current,source=find_current(c,df,provider)
    if current is None:
        return {"available":False,"error":"No data for corresponding previous-year date or configured reference date.","current":None,"historical":[],"forecast":[],"subsystems":[]}
    stamp=current["period_end"]
    horizon=stamp+timedelta(hours=1)
    active=[s for s in c.subsystems if s.active]
    historical=[]
    day_rows=df[(df.period_end.dt.date==stamp.date()) & (df.period_end<=stamp)]
    for _,row in day_rows.iterrows():
        weather=provider.json_record(row); parts,total=calc_at(c,row)
        historical.append({"timestamp":weather["period_end"],"gti":float(row.gti),"air_temp":float(row.air_temp),"subsystems":parts,"combined_kw":total})
    later=df[(df.period_end>stamp) & (df.period_end<=horizon)]
    forecasts=[]
    prior=stamp
    for _,row in later.iterrows():
        weather=provider.json_record(row); parts,total=calc_at(c,row)
        seconds=(row.period_end-prior).total_seconds()
        forecasts.append({"timestamp":weather["period_end"],"gti":float(row.gti),"air_temp":float(row.air_temp),"subsystems":parts,"combined_kw":total,"interval_seconds":seconds,"energy_kwh":energy(total,seconds)})
        prior=row.period_end
    start=provider.json_record(current)
    current_parts,current_total=calc_at(c,current)
    return {"available":True,"selection":source,"horizon_hours":1,"current":{"timestamp":start["period_end"],"gti":float(current["gti"]),"air_temp":float(current["air_temp"]),"subsystems":current_parts,"combined_kw":current_total},"historical":historical,"forecast":forecasts,"subsystems":[{"id":s.id,"name":s.name,"capacity_kw":s.ac_capacity_kw} for s in active]}
