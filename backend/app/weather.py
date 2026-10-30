from abc import ABC, abstractmethod
from pathlib import Path
import pandas as pd
from zoneinfo import ZoneInfo

REQUIRED = {"period_end", "gti", "air_temp"}

class WeatherProvider(ABC):
    @abstractmethod
    def load(self): ...
    @abstractmethod
    def coverage(self): ...
    @abstractmethod
    def record_at(self, timestamp): ...
    @abstractmethod
    def next_record(self, timestamp): ...

class CSVWeatherProvider(WeatherProvider):
    def __init__(self, path: str, timezone="Asia/Kolkata"):
        self.path, self.timezone, self.frame = Path(path), timezone, None
        self.error = None

    def load(self):
        if not self.path.exists():
            self.error = f"Weather CSV not found: {self.path}"
            self.frame = None
            return None
        try:
            df = pd.read_csv(self.path)
            missing = REQUIRED - set(df.columns)
            if missing:
                raise ValueError("Missing required CSV columns: " + ", ".join(sorted(missing)))
            parsed = pd.to_datetime(df["period_end"], errors="coerce", format="mixed")
            # Offset-aware values retain their stated instant; naive values are local plant time.
            if getattr(parsed.dt, "tz", None) is None:
                parsed = parsed.dt.tz_localize(self.timezone, ambiguous="NaT", nonexistent="NaT")
            else:
                parsed = parsed.dt.tz_convert(self.timezone)
            df["period_end"] = parsed
            for col in df.columns:
                if col != "period_end":
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=["period_end", "gti", "air_temp"])
            df = df[df.gti >= 0].sort_values("period_end").drop_duplicates("period_end", keep="last").reset_index(drop=True)
            self.frame, self.error = df, None
            return df
        except Exception as exc:
            self.frame, self.error = None, str(exc)
            return None

    def ensure(self):
        if self.frame is None: self.load()
        return self.frame

    def coverage(self):
        df = self.ensure()
        if df is None or df.empty: return {"available": False, "error": self.error}
        diffs = df.period_end.diff().dropna().dt.total_seconds()
        interval = int(diffs.median()) if len(diffs) else None
        return {"available": True, "provider": "local_csv", "path": str(self.path),
            "start": df.period_end.iloc[0].isoformat(), "end": df.period_end.iloc[-1].isoformat(),
            "records": len(df), "interval_seconds": interval}

    def record_at(self, timestamp):
        df = self.ensure()
        if df is None or df.empty: return None
        stamp = pd.Timestamp(timestamp)
        if stamp.tzinfo is None: stamp = stamp.tz_localize(self.timezone)
        else: stamp = stamp.tz_convert(self.timezone)
        hits = df[df.period_end == stamp]
        return hits.iloc[0].to_dict() if not hits.empty else None

    def next_record(self, timestamp):
        df = self.ensure()
        if df is None or df.empty: return None
        stamp = pd.Timestamp(timestamp)
        if stamp.tzinfo is None: stamp = stamp.tz_localize(self.timezone)
        else: stamp = stamp.tz_convert(self.timezone)
        hits = df[df.period_end > stamp]
        return hits.iloc[0].to_dict() if not hits.empty else None

    def nearest_same_date(self, timestamp):
        df = self.ensure()
        if df is None or df.empty: return None
        stamp = pd.Timestamp(timestamp)
        hits = df[(df.period_end.dt.date == stamp.date())]
        if hits.empty: return None
        hits = hits.assign(delta=(hits.period_end - stamp).abs())
        return hits.sort_values(["delta", "period_end"]).iloc[0].drop(labels="delta").to_dict()

    def series(self, start=None, end=None):
        df = self.ensure()
        if df is None: return []
        if start is not None: df = df[df.period_end >= pd.Timestamp(start)]
        if end is not None: df = df[df.period_end <= pd.Timestamp(end)]
        return [self.json_record(row) for _, row in df.iterrows()]

    @staticmethod
    def json_record(record):
        return {k: (v.isoformat() if hasattr(v, "isoformat") else (None if pd.isna(v) else float(v) if isinstance(v, (int,float)) else v)) for k,v in record.items()}
