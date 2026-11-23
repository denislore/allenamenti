"""
Sonno: lettura dei riepiloghi giornalieri (endpoint /sleep-service/sleep/dailySleepSummariesByDate).

Lo schema della risposta non è pubblicato nella documentazione Garmin consultata, quindi il
parser accetta i nomi di campo dei due formati Garmin noti (Garmin Connect e Health API).
Se non riconosce nulla, salva SOLO i nomi dei campi trovati (mai i valori) in "sonno_diag",
così la dashboard può mostrarli e il parser si corregge sui nomi reali.
"""
from __future__ import annotations

from datetime import datetime, timezone

CAMPI = {
    "totale": ("sleepTimeSeconds", "sleepTimeInSeconds", "durationInSeconds", "sleepDurationInSeconds", "totalSleepSeconds"),
    "profondo": ("deepSleepSeconds", "deepSleepDurationInSeconds", "deepSleepInSeconds"),
    "leggero": ("lightSleepSeconds", "lightSleepDurationInSeconds", "lightSleepInSeconds"),
    "rem": ("remSleepSeconds", "remSleepInSeconds", "remSleepDurationInSeconds"),
    "sveglio": ("awakeSleepSeconds", "awakeDurationInSeconds", "awakeSleepInSeconds", "awakeSeconds"),
}


def _num(d: dict, chiavi) -> float | None:
    for k in chiavi:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _orario_locale(d: dict, chiave_ms: str, chiave_s: str) -> str | None:
    """HH:MM locale. Garmin Connect: timestamp 'Local' in ms; Health API: secondi + offset."""
    v = d.get(chiave_ms)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%H:%M")
    s = d.get(chiave_s)
    if isinstance(s, (int, float)):
        off = d.get("startTimeOffsetInSeconds") or 0
        return datetime.fromtimestamp(s + off, tz=timezone.utc).strftime("%H:%M")
    return None


def _punteggio(d: dict) -> tuple[float | None, str | None]:
    sc = d.get("sleepScores")
    if isinstance(sc, dict):
        o = sc.get("overall") or sc.get("overallScore")
        if isinstance(o, dict):
            return _num(o, ("value",)), o.get("qualifierKey")
        if isinstance(o, (int, float)):
            return float(o), None
    o = d.get("overallSleepScore")
    if isinstance(o, dict):
        return _num(o, ("value",)), o.get("qualifierKey")
    return _num(d, ("sleepScore", "overallSleepScoreValue")), None


def _elementi(resp) -> list[dict]:
    if isinstance(resp, list):
        return [x for x in resp if isinstance(x, dict)]
    if isinstance(resp, dict):
        if "dailySleepDTO" in resp or "calendarDate" in resp:
            return [resp]
        for v in resp.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def normalizza(resp) -> list[dict]:
    out = []
    for el in _elementi(resp):
        d = el.get("dailySleepDTO") if isinstance(el.get("dailySleepDTO"), dict) else el
        data = d.get("calendarDate") or el.get("calendarDate")
        tot = _num(d, CAMPI["totale"])
        if not data or not tot:
            continue
        val, qual = _punteggio(d)
        out.append({
            "data": str(data)[:10],
            "totale": round(tot),
            "profondo": _num(d, CAMPI["profondo"]),
            "leggero": _num(d, CAMPI["leggero"]),
            "rem": _num(d, CAMPI["rem"]),
            "sveglio": _num(d, CAMPI["sveglio"]),
            "inizio": _orario_locale(d, "sleepStartTimestampLocal", "startTimeInSeconds"),
            "fine": _orario_locale(d, "sleepEndTimestampLocal", "endTimeInSeconds"),
            "punteggio": val,
            "qualita": qual,
        })
    return out


def chiavi(resp, prefisso: str = "", profondita: int = 3) -> list[str]:
    """Solo i NOMI dei campi (mai i valori), per la diagnostica."""
    out = set()
    if profondita <= 0:
        return []
    if isinstance(resp, list):
        for x in resp[:3]:
            out.update(chiavi(x, prefisso + "[]", profondita))
    elif isinstance(resp, dict):
        for k, v in resp.items():
            p = f"{prefisso}.{k}" if prefisso else str(k)
            out.add(p)
            if isinstance(v, (dict, list)):
                out.update(chiavi(v, p, profondita - 1))
    return sorted(out)[:150]
