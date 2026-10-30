from datetime import datetime
import pandas as pd
from app.models import default_config, PlantConfig
from app.engine import power, energy
from app.weather import CSVWeatherProvider
from fastapi.testclient import TestClient
from app import main
from zoneinfo import ZoneInfo
from datetime import timedelta

def test_default_capacity_and_count():
    c=default_config()
    assert len(c.subsystems)==2 and c.capacity_kw==100

def test_pr_engine_caps_and_zero():
    s=default_config().subsystems[0]
    assert power(s,{"gti":0,"air_temp":20})==0
    assert power(s,{"gti":1000,"air_temp":20})==40
    assert power(s,{"gti":5000,"air_temp":20})==50

def test_temperature_model_uses_dc_nameplate_and_efficiency():
    s=default_config().subsystems[0]
    got=power(s,{"gti":1000,"air_temp":25},"temperature")
    assert 0 < got < s.module_wattage*s.module_count/1000

def test_energy_interval():
    assert energy(10,1800)==5

def test_csv_sort_duplicates_and_interval(tmp_path):
    f=tmp_path/'w.csv';f.write_text('period_end,gti,air_temp\n2024-01-01T00:30:00Z,200,20\n2024-01-01T00:00:00Z,100,18\n2024-01-01T00:30:00Z,250,21\n')
    p=CSVWeatherProvider(str(f)); assert len(p.ensure())==2
    assert p.coverage()['interval_seconds']==1800
    assert p.ensure().iloc[-1].gti==250

def test_missing_csv_clear_error(tmp_path):
    p=CSVWeatherProvider(str(tmp_path/'missing.csv'))
    assert p.coverage()['available'] is False and 'not found' in p.error

def test_required_columns_error(tmp_path):
    f=tmp_path/'bad.csv'; f.write_text('period_end,gti\n2024-01-01,10\n')
    p=CSVWeatherProvider(str(f))
    assert p.coverage()['available'] is False
    assert 'air_temp' in p.error

def test_next_record_is_chronological(tmp_path):
    f=tmp_path/'w.csv'; f.write_text('period_end,gti,air_temp\n2024-01-01T00:30:00Z,200,20\n2024-01-01T00:00:00Z,100,18\n')
    p=CSVWeatherProvider(str(f))
    r=p.next_record('2024-01-01T05:30:00+05:30')
    assert r['gti']==200

def test_capacity_updates_with_subsystem_changes():
    c=default_config(); c.subsystems[1].ac_capacity_kw=35
    c.subsystems[1].inverter_rating_kw=35
    c.identical_subsystems=False
    assert c.capacity_kw==85
    c.subsystems.pop()
    assert c.capacity_kw==50

def test_dashboard_uses_csv_and_persisted_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'CONFIG_PATH', tmp_path/'plant.json')
    csv_path=tmp_path/'weather.csv'
    now=datetime.now(ZoneInfo('Asia/Kolkata'))
    target=now.replace(year=now.year-1, second=0, microsecond=0)
    following=target+timedelta(minutes=15)
    csv_path.write_text('period_end,gti,air_temp\n'+target.isoformat()+',500,25\n'+following.isoformat()+',600,26\n')
    monkeypatch.setattr(main, 'CSV_PATH', csv_path)
    client=TestClient(main.app)
    c=client.get('/api/config').json()
    c['subsystems'][0]['performance_ratio']=0.7
    c['subsystems'][1]['performance_ratio']=0.7
    c['shared']['performance_ratio']=0.7
    assert client.put('/api/config',json=c).status_code==200
    result=client.get('/api/dashboard').json()
    assert result['available'] is True
    assert result['current']['power_kw']==35
    assert result['forecast']['power_kw']==42
    assert result['forecast']['timestamp']==following.isoformat()
    assert len(result['series'])==1
    assert result['series'][0]['timestamp']==target.isoformat()
    outlook=client.get('/api/forecast').json()
    assert outlook['available'] is True
    assert outlook['horizon_hours']==1
    assert len(outlook['forecast'])==1
    assert outlook['forecast'][0]['combined_kw']==42
    assert outlook['forecast'][0]['energy_kwh']==10.5
    monkeypatch.setattr(main, 'fetch_karad_weather', lambda: {"available":True,"city":"Karad","temperature_c":33.5,"timestamp":"2026-10-25T14:00","source":"test"})
    live=client.get('/api/weather/karad-current').json()
    assert live['temperature_c']==33.5
    assert outlook['forecast'][0]['air_temp']==26

def test_weather_csv_upload_validates_and_activates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'ROOT', tmp_path)
    monkeypatch.setattr(main, 'CSV_PATH', tmp_path/'missing.csv')
    client=TestClient(main.app)
    response=client.post('/api/weather/upload',files={'file':('site.csv','period_end,gti,air_temp\n2024-01-01T00:00:00Z,100,20\n','text/csv')})
    assert response.status_code==200
    assert response.json()['status']['available'] is True
    uploaded=tmp_path/'data'/'uploads'/'site.csv'
    assert uploaded.read_text()=='period_end,gti,air_temp\n2024-01-01T00:00:00Z,100,20\n'
    bad=client.post('/api/weather/upload',files={'file':('bad.csv','period_end,gti\n2024-01-01,10\n','text/csv')})
    assert bad.status_code==400
    assert client.get('/api/weather/status').json()['available'] is True
