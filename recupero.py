"""
Recupero giornaliero: FC a riposo, stress medio, Body Battery, passi, VO2max.

I nomi dei campi della Personal API non sono documentati qui: il lettore è tollerante (cerca più nomi possibili,
anche annidati) e, se in una risposta non trova nulla, restituisce solo i NOMI dei campi per la diagnostica
(mai i valori).
"""
from __future__ import annotations

CAMPI = {
    "fc_riposo": ("restingHeartRateInBeatsPerMinute", "restingHeartRate", "restingHr", "restingHeartRateValue", "rhr"),
    "stress": ("averageStressLevel", "avgStressLevel", "overallStressLevel", "averageStress"),
    "bb_max": ("bodyBatteryHighestValue", "highestBodyBattery", "bodyBatteryHigh", "maxBodyBattery", "bodyBatteryMax"),
    "bb_min": ("bodyBatteryLowestValue", "lowestBodyBattery", "bodyBatteryLow", "minBodyBattery", "bodyBatteryMin"),
    "bb_risveglio": ("bodyBatteryAtWakeTime", "bodyBatteryWakeValue", "wakeBodyBattery"),
    "bb_carica": ("bodyBatteryChargedValue", "bodyBatteryCharged", "charged"),
    "bb_scarica": ("bodyBatteryDrainedValue", "bodyBatteryDrained", "drained"),
    "passi": ("steps", "totalSteps", "stepCount", "totalStepCount", "dailyStepCount"),
    "vo2": ("vo2MaxPreciseValue", "vo2MaxValue", "vO2MaxValue", "vo2Max", "vo2max"),
}
CHIAVI_DATA = ("calendarDate", "summaryDate", "date", "day", "calendarDay")
_INDICE = {nome.lower(): campo for campo, nomi in CAMPI.items() for nome in nomi}


def _elementi(resp) -> list[dict]:
    if isinstance(resp, list):
        return [x for x in resp if isinstance(x, dict)]
    if isinstance(resp, dict):
        if any(k in resp for k in CHIAVI_DATA):
            return [resp]
        for v in resp.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        for v in resp.values():                       # un livello più in basso
            if isinstance(v, dict):
                el = _elementi(v)
                if el:
                    return el
    return []


def _data(r: dict) -> str | None:
    for k in CHIAVI_DATA:
        v = r.get(k)
        if isinstance(v, str) and len(v) >= 10:
            return v[:10]
    for v in r.values():
        if isinstance(v, dict):
            d = _data(v)
            if d:
                return d
    return None


def _serie_numeri(v) -> list[float]:
    """Valori numerici di una serie temporale: {offset: valore}, [[ts, valore], ...] o [valore, ...]."""
    out = []
    if isinstance(v, dict):
        out = [x for x in v.values() if isinstance(x, (int, float)) and not isinstance(x, bool)]
    elif isinstance(v, list):
        for x in v:
            if isinstance(x, (list, tuple)) and len(x) >= 2:      # [ts, livello] oppure [ts, "MEASURED", livello, versione]
                num = [y for y in x[1:] if isinstance(y, (int, float)) and not isinstance(y, bool) and 0 <= y <= 100]
                if num:
                    out.append(num[0])
            elif isinstance(x, (int, float)) and not isinstance(x, bool):
                out.append(x)
            elif isinstance(x, dict):
                for k in ("value", "level", "bodyBatteryLevel"):
                    if isinstance(x.get(k), (int, float)):
                        out.append(x[k]); break
    return [x for x in out if 0 <= x <= 100]


def _numero(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _dentro(v: dict):
    """Valore di un campo strutturato, es. {"value": 48} o {"total": 9000}."""
    for k in ("value", "total", "count", "average", "avg", "restingValue"):
        if _numero(v.get(k)) is not None:
            return v[k]
    return None


def _fc_riposo_generica(k: str) -> bool:
    """Nomi non previsti ma inequivocabili, es. "restingHeartRateBpm"; esclude le medie di più giorni."""
    k = k.lower()
    return "resting" in k and ("heartrate" in k or k.endswith("hr")) and not any(x in k for x in ("avg", "average", "seven", "7"))


def _cerca(r: dict, trovati: dict, profondita: int = 4) -> None:
    if profondita <= 0:
        return
    for k, v in r.items():
        campo = _INDICE.get(k.lower()) or ("fc_riposo" if _fc_riposo_generica(k) else None)
        if campo and isinstance(v, dict) and _dentro(v) is not None and campo not in trovati:
            v = _dentro(v)
        if campo and _numero(v) is not None and campo not in trovati:
            if campo == "fc_riposo" and not 25 <= v <= 120:
                continue
            trovati[campo] = round(float(v), 1)
        elif isinstance(v, dict) and not (campo and campo in trovati):
            if "bodybattery" in k.lower() and "bb_max" not in trovati and _serie_numeri(v):
                s = _serie_numeri(v)
                trovati.setdefault("bb_max", max(s)); trovati.setdefault("bb_min", min(s)); trovati.setdefault("bb_ultimo", s[-1])
            else:
                _cerca(v, trovati, profondita - 1)
        elif isinstance(v, list) and "bodybattery" in k.lower() and "bb_max" not in trovati:
            s = _serie_numeri(v)
            if s:
                trovati.setdefault("bb_max", max(s)); trovati.setdefault("bb_min", min(s)); trovati.setdefault("bb_ultimo", s[-1])


def normalizza(resp) -> list[dict]:
    """[{data, fc_riposo, stress, bb_max, ...}] — solo i campi trovati."""
    out = []
    for r in _elementi(resp):
        d = _data(r)
        if not d:
            continue
        trovati: dict = {}
        _cerca(r, trovati)
        if trovati:
            out.append({"data": d, **trovati})
    return out


def unisci(*liste: list[dict]) -> list[dict]:
    per_data: dict[str, dict] = {}
    for lista in liste:
        for r in lista:
            g = per_data.setdefault(r["data"], {"data": r["data"]})
            for k, v in r.items():
                if v is not None and k not in g:
                    g[k] = v
    return sorted(per_data.values(), key=lambda r: r["data"])
