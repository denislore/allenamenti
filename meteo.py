"""
Meteo di ogni uscita da Open-Meteo (https://open-meteo.com): gratuito per uso non commerciale, senza chiave,
dati con licenza CC BY 4.0. Vengono inviate solo coordinate approssimate (circa 100 m) e date dell'uscita.

Risultato: {"t": temperatura °C, "tp": percepita °C, "u": umidità %, "v": vento km/h, "p": pioggia mm, "c": codice WMO}
media sulle ore dell'uscita (pioggia: somma).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta

URL_RECENTE = "https://api.open-meteo.com/v1/forecast"          # ultimi ~3 mesi
URL_STORICO = "https://archive-api.open-meteo.com/v1/archive"   # storico (con qualche giorno di ritardo)
VARIABILI = "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,wind_speed_10m,weather_code"
GIORNI_RECENTI = 60


def _get(url: str, params: dict, timeout: float = 20) -> dict:
    richiesta = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                       headers={"User-Agent": "allenamenti-dashboard (uso personale)"})
    with urllib.request.urlopen(richiesta, timeout=timeout) as r:
        return json.load(r)


def scarica(lat: float, lon: float, inizio: str, durata_s: float | None, oggi: date | None = None) -> dict | None:
    """inizio: ora locale "YYYY-MM-DD HH:MM:SS" (come in Garmin)."""
    t0 = datetime.fromisoformat(str(inizio).replace(" ", "T")[:19])
    t1 = t0 + timedelta(seconds=max(durata_s or 0, 1800))
    oggi = oggi or date.today()
    params = {"latitude": round(lat, 3), "longitude": round(lon, 3), "hourly": VARIABILI, "timezone": "auto",
              "start_date": t0.date().isoformat(), "end_date": t1.date().isoformat()}
    url = URL_RECENTE if (oggi - t0.date()).days <= GIORNI_RECENTI else URL_STORICO
    return riassumi(_get(url, params), t0, t1)


def riassumi(dati: dict, t0: datetime, t1: datetime) -> dict | None:
    ore = (dati or {}).get("hourly") or {}
    tempi = ore.get("time") or []
    da = t0.replace(minute=0, second=0, microsecond=0)
    sel = [i for i, h in enumerate(tempi) if da <= datetime.fromisoformat(h) <= t1]
    if not sel:
        return None

    def valori(k):
        serie = ore.get(k) or []
        return [serie[i] for i in sel if i < len(serie) and isinstance(serie[i], (int, float))]

    def media(k, cifre=1):
        v = valori(k)
        return round(sum(v) / len(v), cifre) if v else None

    codici = valori("weather_code")
    out = {"t": media("temperature_2m"), "tp": media("apparent_temperature"),
           "u": media("relative_humidity_2m", 0), "v": media("wind_speed_10m", 0),
           "p": round(sum(valori("precipitation")), 1) if valori("precipitation") else None,
           "c": Counter(int(c) for c in codici).most_common(1)[0][0] if codici else None}
    return out if out["t"] is not None else None
