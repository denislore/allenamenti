"""
Recupero giornaliero: FC a riposo, stress medio, Body Battery, passi, VO2max.

I nomi dei campi della Personal API non sono documentati qui: il lettore è tollerante (cerca più nomi possibili,
anche annidati) e, se in una risposta non trova nulla, restituisce solo i NOMI dei campi per la diagnostica
(mai i valori).
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("Europe/Rome")

CAMPI = {
    "fc_riposo": ("restingHeartRateInBeatsPerMinute", "restingHeartRate", "restingHr", "restingHeartRateValue", "rhr"),
    "stress": ("averageStressLevel", "avgStressLevel", "overallStressLevel", "averageStress"),
    "bb_max": ("bodyBatteryHighestValue", "highestBodyBattery", "bodyBatteryHigh", "maxBodyBattery", "bodyBatteryMax"),
    "bb_min": ("bodyBatteryLowestValue", "lowestBodyBattery", "bodyBatteryLow", "minBodyBattery", "bodyBatteryMin"),
    "bb_risveglio": ("bodyBatteryAtWakeTime", "bodyBatteryWakeValue", "wakeBodyBattery"),
    "bb_ultimo": ("bodyBatteryMostRecentValue", "mostRecentBodyBattery"),
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


def _epoca(ts) -> float | None:
    """Secondi epoch da un timestamp Garmin (secondi o millisecondi)."""
    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or ts < 1e9:
        return None
    return ts / 1000 if ts > 1e11 else float(ts)


def _punti_bb(r: dict, profondita: int = 4) -> list[tuple[float, float]]:
    """Serie Body Battery con l'orario di ogni punto: [(epoch_s, livello)].
    Formati gestiti: {offset_s: livello} + startTimeInSeconds (Health/Personal API),
    [[ts_ms, livello]] o [[ts_ms, "MEASURED", livello, versione]] (Garmin Connect), [{timestamp, value}]."""
    if profondita <= 0 or not isinstance(r, dict):
        return []
    base = _epoca(r.get("startTimeInSeconds"))
    for k, v in r.items():
        if "bodybattery" not in k.lower():
            continue
        punti = []
        if isinstance(v, dict) and base is not None:
            for off, liv in v.items():
                try:
                    off = float(off)
                except (TypeError, ValueError):
                    continue
                if _numero(liv) is not None and 0 <= liv <= 100:
                    punti.append((base + off, float(liv)))
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, (list, tuple)) and len(x) >= 2:
                    t = _epoca(x[0])
                    num = [y for y in x[1:] if _numero(y) is not None and 0 <= y <= 100]
                    if t and num:
                        punti.append((t, float(num[0])))
                elif isinstance(x, dict):
                    t = next((_epoca(x.get(c)) for c in ("timestamp", "timestampGMT", "startTimestampGMT") if _epoca(x.get(c))), None)
                    liv = next((x[c] for c in ("value", "level", "bodyBatteryLevel") if _numero(x.get(c)) is not None), None)
                    if t and liv is not None and 0 <= liv <= 100:
                        punti.append((t, float(liv)))
        if len(punti) >= 2:
            return sorted(punti)
    for v in r.values():
        if isinstance(v, dict):
            p = _punti_bb(v, profondita - 1)
            if p:
                return p
    return []


def _minuti_locali(punti: list[tuple[float, float]], giorno: str) -> list[list[float]]:
    """[[minuti dalla mezzanotte locale di `giorno`, livello]] (ora italiana)."""
    mezzanotte = datetime.fromisoformat(giorno).replace(tzinfo=FUSO)
    return [[round((datetime.fromtimestamp(t, tz=timezone.utc) - mezzanotte).total_seconds() / 60), v] for t, v in punti]


def normalizza(resp) -> list[dict]:
    """[{data, fc_riposo, stress, bb_max, ...}] — solo i campi trovati.
    Se c'è la serie della Body Battery la conserva in `_bb_serie` (temporanea, la toglie `risveglio`)."""
    out = []
    for r in _elementi(resp):
        d = _data(r)
        if not d:
            continue
        trovati: dict = {}
        _cerca(r, trovati)
        punti = _punti_bb(r)
        if punti:
            trovati["_bb_serie"] = _minuti_locali(punti, d)
        if trovati:
            out.append({"data": d, **trovati})
    return out


def _hhmm(s) -> int | None:
    try:
        h, m = str(s).split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def risveglio(recupero: list[dict], sonno: list[dict]) -> int:
    """Body Battery al risveglio = livello della serie all'ora in cui finisce il sonno (come fa Garmin Connect).
    Garmin non manda il valore già pronto. Toglie sempre `_bb_serie`. Restituisce quanti giorni ha completato."""
    fine = {s["data"]: _hhmm(s.get("fine")) for s in sonno if s.get("data")}
    fatti = 0
    for r in recupero:
        serie = r.pop("_bb_serie", None)
        m = fine.get(r["data"])
        if r.get("bb_risveglio") is not None or not serie or m is None:
            continue
        if m > 14 * 60:                                   # risveglio "dopo mezzogiorno": orario sospetto, meglio niente
            continue
        vicini = [p for p in serie if abs(p[0] - m) <= 60]
        if not vicini:
            continue
        dopo = [p for p in vicini if p[0] >= m]
        p = min(dopo, key=lambda p: p[0]) if dopo else max(vicini, key=lambda p: p[0])
        r["bb_risveglio"] = round(p[1])
        fatti += 1
    return fatti


def ultime_24h(recupero: list[dict], adesso: float, passo_min: int = 15) -> list[list[int]]:
    """Andamento delle ultime 24 ore: [[epoch_ms, livello]], un punto ogni `passo_min` minuti (l'ultimo di ogni intervallo).
    Va chiamata prima di `risveglio`, che toglie le serie."""
    punti = {}
    for r in recupero:
        serie = r.get("_bb_serie")
        if not serie:
            continue
        mezzanotte = datetime.fromisoformat(r["data"]).replace(tzinfo=FUSO).timestamp()
        for m, v in serie:
            t = mezzanotte + m * 60
            if adesso - 86400 <= t <= adesso + 600:
                punti[t] = v
    blocchi = {}
    for t in sorted(punti):
        blocchi[int(t // (passo_min * 60))] = (t, punti[t])
    return [[int(t * 1000), round(v)] for t, v in blocchi.values()]


def unisci(*liste: list[dict]) -> list[dict]:
    per_data: dict[str, dict] = {}
    for lista in liste:
        for r in lista:
            g = per_data.setdefault(r["data"], {"data": r["data"]})
            for k, v in r.items():
                if v is not None and k not in g:
                    g[k] = v
    return sorted(per_data.values(), key=lambda r: r["data"])
