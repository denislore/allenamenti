#!/usr/bin/env python3
"""
Sincronizza le attività Garmin Connect (Personal API ufficiale) e genera
docs/data.enc (riepiloghi) e docs/tracce.enc (tracce GPS): file JSON compressi e CIFRATI (AES-256-GCM, chiave derivata
dalla password con PBKDF2-SHA256), letto dalla dashboard sul telefono.

Uso:
    python sync.py              aggiornamento (elenco attività completo, HRV e tracce GPS incrementali)
    python sync.py --completo   riscarica anche tutto lo storico HRV
    python sync.py --esplora    (solo sul PC, mai su GitHub) salva in ./esplora
                                le risposte grezze dell'API per verificarne i campi

Variabili d'ambiente richieste:
    GARMIN_PERSONAL_API_KEY   chiave creata su Garmin Connect Web
    DASHBOARD_PASSWORD        password con cui cifrare i dati

IMPORTANTE: il repository è pubblico e anche i log di GitHub Actions lo sono.
Questo script non stampa MAI dati personali, solo conteggi.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from garmin_personal_api import (
    GarminPersonalApiClient,
    GarminPersonalApiError,
    HttpError,
    PersonalApiAuthenticator,
)

import analisi as an
import meteo as mt
import notifiche as nt
import percorsi as perc
import recupero as rec
import sonno as snn

ROOT = Path(__file__).resolve().parent
OUT_FILE = ROOT / "docs" / "data.enc"
TRACCE_FILE = ROOT / "docs" / "tracce.enc"
ABBINAMENTI_FILE = ROOT / "docs" / "abbinamenti.enc"   # scritto dall'app quando modifichi i percorsi
NOTIFICHE_FILE = ROOT / "docs" / "notifiche.enc"         # scritto dall'app quando attivi le notifiche
STATO_FILE = ROOT / "docs" / "stato.json"                # esito dell'ultimo giro (in chiaro: solo orario ed esito)
MAX_METEO_PER_GIRO = 60
PBKDF2_ITER = 310_000
PAGE_SIZE = 100
MAX_TRACCE_PER_GIRO = 40        # download .FIT per esecuzione: lo storico si completa in più giri
MAX_TRACCE_STORICO = 150        # ...ma con un lungo storico ancora da scaricare si va più veloci (circa 5 minuti per giro)
SOGLIA_STORICO = 200            # uscite ancora da scaricare oltre le quali vale il limite più alto
MAX_METEO_STORICO = 250
MAX_TENTATIVI_TRACCIA = 3
STORICO_HRV_GG = 365
BLOCCO_HRV_GG = 28              # richieste HRV a blocchi di 4 settimane
PAUSA_S = 0.5                   # pausa tra richieste, per non stressare l'API


# --------------------------------------------------------------------------- #
# Classificazione sport
# --------------------------------------------------------------------------- #
def classifica(type_key: str | None) -> str:
    """Raggruppa i typeKey Garmin nelle 4 famiglie della dashboard."""
    k = (type_key or "").lower()
    if "run" in k:                                   # running, trail_running, treadmill_running...
        return "corsa"
    if "backcountry" in k or "touring" in k or "skimo" in k:
        return "scialpinismo"
    if "ski" in k or "snowboard" in k:               # resort_skiing..., cross_country_skiing...
        return "sci"
    if "hik" in k or "mountaineer" in k:             # hiking, mountaineering
        return "trekking"
    return "altro"


def _num(a: dict, *chiavi: str) -> float | None:
    for k in chiavi:
        v = a.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return round(float(v), 2)
    return None


def normalizza(a: dict) -> dict | None:
    """Estrae solo i campi usati dalla dashboard. Unità: metri, secondi, m/s."""
    inizio = a.get("startTimeLocal")
    if not inizio or a.get("activityId") is None:
        return None
    t = a.get("activityType")
    type_key = t.get("typeKey") if isinstance(t, dict) else (str(t) if t else None)
    return {
        "id": a.get("activityId"),
        "nome": a.get("activityName") or "",
        "inizio": str(inizio)[:19],                  # "YYYY-MM-DD HH:MM:SS" ora locale
        "tipo": type_key,
        "sport": classifica(type_key),
        "dist": _num(a, "distance"),
        "durata": _num(a, "duration", "elapsedDuration"),
        "mov": _num(a, "movingDuration", "duration"),
        "dplus": _num(a, "elevationGain"),
        "dminus": _num(a, "elevationLoss"),
        "qmax": _num(a, "maxElevation"),
        "fc": _num(a, "averageHR"),
        "fcmax": _num(a, "maxHR"),
        "vmax": _num(a, "maxSpeed"),
        "kcal": _num(a, "calories"),
        "te": _num(a, "aerobicTrainingEffect"),
        "carico": _num(a, "activityTrainingLoad"),
        "vo2": _num(a, "vO2MaxValue"),
        "luogo": a.get("locationName"),
    }


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def _lista(resp) -> list:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in ("activityList", "activities", "data"):
            if isinstance(resp.get(k), list):
                return resp[k]
    return []


def scarica_attivita(client: GarminPersonalApiClient, dal: date | None) -> list[dict]:
    grezze, start = [], 0
    for _ in range(500):                             # tetto di sicurezza: 50.000 attività
        pagina = _lista(client.search_activities(start=start, limit=PAGE_SIZE, start_date=dal))
        if not pagina:
            break
        grezze.extend(pagina)
        start += len(pagina)
        time.sleep(PAUSA_S)
    return [n for n in (normalizza(a) for a in grezze) if n]


def scarica_hrv(client: GarminPersonalApiClient, dal: date, al: date) -> list[dict]:
    """HRV notturna. Se l'endpoint non risponde o il formato è diverso, restituisce []."""
    out, inizio = [], dal
    while inizio <= al:
        fine = min(inizio + timedelta(days=BLOCCO_HRV_GG - 1), al)
        try:
            resp = client.get_daily_hrv_summaries(inizio, fine)
        except (HttpError, GarminPersonalApiError) as e:
            print(f"  HRV non disponibile ({type(e).__name__}); sezione saltata.")
            return []
        righe = resp.get("hrvSummaries") if isinstance(resp, dict) else resp
        for r in righe or []:
            if isinstance(r, dict) and r.get("calendarDate"):
                out.append({
                    "data": r["calendarDate"],
                    "notte": _num(r, "lastNightAvg"),
                    "sett": _num(r, "weeklyAvg"),
                    "stato": r.get("status"),
                })
        inizio = fine + timedelta(days=1)
        time.sleep(PAUSA_S)
    return out


def scarica_sonno(client: GarminPersonalApiClient, dal: date, al: date) -> tuple[list[dict], list[str] | None]:
    """Riepiloghi del sonno. Se il formato non è riconosciuto restituisce anche i nomi dei campi trovati."""
    out, diag, inizio = [], None, dal
    while inizio <= al:
        fine = min(inizio + timedelta(days=BLOCCO_HRV_GG - 1), al)
        try:
            resp = client.get_daily_sleep_summaries(inizio, fine)
        except (HttpError, GarminPersonalApiError) as e:
            print(f"  Sonno non disponibile ({type(e).__name__} {getattr(e, 'status_code', '')}); sezione saltata.")
            return out, diag
        righe = snn.normalizza(resp)
        if not righe and resp and diag is None:
            diag = snn.chiavi(resp)
        out += righe
        inizio = fine + timedelta(days=1)
        time.sleep(PAUSA_S)
    return out, (diag if not out else None)


# --------------------------------------------------------------------------- #
# Cifratura (compatibile WebCrypto: PBKDF2-SHA256 -> AES-256-GCM)
# --------------------------------------------------------------------------- #
def _chiave(password: str, salt: bytes, iterazioni: int) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                      iterations=iterazioni).derive(password.encode("utf-8"))


def cifra(payload: dict, password: str) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    chiaro = gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    ct = AESGCM(_chiave(password, salt, PBKDF2_ITER)).encrypt(iv, chiaro, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": PBKDF2_ITER, "gzip": True,
            "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def decifra(blob: dict, password: str) -> dict:
    d = lambda s: base64.b64decode(s)
    chiaro = AESGCM(_chiave(password, d(blob["salt"]), int(blob["iter"]))).decrypt(d(blob["iv"]), d(blob["ct"]), None)
    if blob.get("gzip"):
        chiaro = gzip.decompress(chiaro)
    return json.loads(chiaro)


def leggi_precedente(password: str, file: Path = OUT_FILE) -> dict | None:
    if not file.exists():
        return None
    try:
        return decifra(json.loads(file.read_text("utf-8")), password)
    except Exception:
        print(f"  {file.name} non decifrabile (password cambiata?): lo rigenero.")
        return None


def scrivi_se_cambiato(file: Path, payload: dict, prec: dict | None, password: str) -> bool:
    if prec is not None and {k: prec.get(k) for k in payload} == payload:
        return False
    file.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "aggiornato": datetime.now().astimezone().isoformat(timespec="minutes")}
    file.write_text(json.dumps(cifra(payload, password)), "utf-8")
    return True


def aggiorna_tracce(client: GarminPersonalApiClient, attivita: list[dict], prec: dict | None) -> dict:
    """
    Scarica i .FIT mancanti (al massimo MAX_TRACCE_PER_GIRO per esecuzione) e ne calcola l'analisi
    (parziali, migliori tempi, VAM). Le tracce scaricate prima dell'analisi vengono riscaricate a poco a poco
    per completarla, dopo quelle nuove e nello stesso limite per giro.
    """
    ids_validi = {str(a["id"]) for a in attivita}
    tracce = {k: v for k, v in (prec or {}).get("tracce", {}).items() if k in ids_validi}
    senza = {k: v for k, v in (prec or {}).get("senza", {}).items() if k in ids_validi}
    analisi = {k: v for k, v in (prec or {}).get("analisi", {}).items() if k in ids_validi}
    an_falliti = {k: v for k, v in (prec or {}).get("an_falliti", {}).items() if k in ids_validi}
    mancanti = [a for a in attivita                           # dalla più recente
                if str(a["id"]) not in tracce and senza.get(str(a["id"]), 0) < MAX_TENTATIVI_TRACCIA]
    da_analizzare = [a for a in attivita if str(a["id"]) in tracce
                     and (analisi.get(str(a["id"])) or {}).get("v") != an.VERSIONE
                     and an_falliti.get(str(a["id"]), 0) < MAX_TENTATIVI_TRACCIA]
    scaricate = analizzate = 0
    limite = MAX_TRACCE_PER_GIRO if len(mancanti) + len(da_analizzare) <= SOGLIA_STORICO else max(MAX_TRACCE_PER_GIRO, MAX_TRACCE_STORICO)
    for a in mancanti + da_analizzare:
        k = str(a["id"])
        nuova = k not in tracce
        if nuova and not perc.da_scaricare(a):
            senza[k] = 99                                     # attività indoor: niente GPS
            continue
        if scaricate >= limite:
            break
        try:
            contenuto = client.download_activity(a["id"])
            scaricate += 1
        except HttpError as e:
            if e.status_code == 429:
                print("  Limite di richieste Garmin raggiunto: riprendo al prossimo giro.")
                break
            (senza if nuova else an_falliti)[k] = (senza if nuova else an_falliti).get(k, 0) + 1
            continue
        except GarminPersonalApiError:
            (senza if nuova else an_falliti)[k] = (senza if nuova else an_falliti).get(k, 0) + 1
            continue
        try:
            pts, ris = perc.estrai(contenuto, a["sport"])
        except Exception:
            pts, ris = None, None
            if nuova:
                senza[k] = senza.get(k, 0) + 1
        if nuova:
            if pts:
                tracce[k] = pts
                senza.pop(k, None)
            elif k not in senza:
                senza[k] = 99                                 # file senza posizioni GPS
        if ris:
            analisi[k] = ris
            an_falliti.pop(k, None)
            analizzate += 1
        elif pts or not nuova:
            an_falliti[k] = an_falliti.get(k, 0) + 1
        time.sleep(PAUSA_S)
    restano = sum(1 for a in attivita if str(a["id"]) not in tracce and senza.get(str(a["id"]), 0) < MAX_TENTATIVI_TRACCIA
                  and perc.da_scaricare(a))
    da_fare = sum(1 for k in tracce if (analisi.get(k) or {}).get("v") != an.VERSIONE and an_falliti.get(k, 0) < MAX_TENTATIVI_TRACCIA)
    print(f"  Tracce: {scaricate} file scaricati in questo giro, {len(tracce)} tracce totali, {restano} ancora da scaricare; "
          f"analisi: {analizzate} nuove, {da_fare} ancora da completare.")
    return {"tracce": tracce, "senza": senza, "analisi": analisi, "an_falliti": an_falliti}


CAMPI_RECUPERO: dict = {}
MAX_FC_PER_GIRO = 40            # giorni di FC a riposo chiesti uno per uno a ogni giro (se il riepilogo non la contiene)


def aggiorna_fc_riposo(client: GarminPersonalApiClient, recupero: list[dict], prec_fc: dict | None, oggi: date) -> dict:
    """FC a riposo giorno per giorno dal servizio frequenza cardiaca, solo se il riepilogo giornaliero non la riporta.
    Restituisce {data: bpm | None}; i giorni recenti senza valore vengono richiesti di nuovo (il giorno non è finito)."""
    fc = dict(prec_fc or {})
    recenti = [r for r in recupero if r["data"] >= (oggi - timedelta(days=14)).isoformat()]
    if recenti and sum(1 for r in recenti if r.get("fc_riposo") is not None) >= len(recenti) / 2:
        return fc                                              # il riepilogo la contiene già
    ritenta = (oggi - timedelta(days=2)).isoformat()
    giorni = [(oggi - timedelta(days=i)).isoformat() for i in range(STORICO_HRV_GG)]
    da_fare = [g for g in giorni if g not in fc or (fc[g] is None and g >= ritenta)][:MAX_FC_PER_GIRO]
    errori = 0
    for g in da_fare:
        try:
            righe = rec.normalizza(client.get_daily_heart_rate(g))
        except Exception as e:
            print(f"  FC a riposo: non disponibile ({type(e).__name__} {getattr(e, 'status_code', '')}).")
            errori += 1
            if errori >= 3 or getattr(e, "status_code", None) in (403, 404, 429):
                break
            continue
        v = next((r.get("fc_riposo") for r in righe if r.get("fc_riposo") is not None), None)
        fc[g] = v
        time.sleep(PAUSA_S)
    limite = (oggi - timedelta(days=STORICO_HRV_GG)).isoformat()
    fc = {g: v for g, v in fc.items() if g >= limite}
    mancano = sum(1 for g in giorni if g not in fc)
    print(f"  FC a riposo: {len(da_fare)} giorni richiesti, {sum(1 for v in fc.values() if v)} con valore, {mancano} ancora da chiedere.")
    return fc


def unisci_fc(recupero: list[dict], fc: dict) -> list[dict]:
    per_data = {r["data"]: dict(r) for r in recupero}
    for g, v in fc.items():
        if v is not None:
            r = per_data.setdefault(g, {"data": g})
            r.setdefault("fc_riposo", v)
    return sorted(per_data.values(), key=lambda r: r["data"])


def scarica_recupero(client: GarminPersonalApiClient, dal: date, al: date) -> tuple[list[dict], dict | None]:
    """FC a riposo, stress, Body Battery, passi e VO2max giornalieri. Ogni fonte è facoltativa."""
    fonti = {"riepiloghi": client.get_daily_summaries, "body_battery": client.get_daily_body_battery,
             "stress": client.get_daily_stress, "vo2max": client.get_daily_vo2_max}
    liste, diag = [], {}
    CAMPI_RECUPERO.clear()
    for nome, f in fonti.items():
        trovate, inizio = [], dal
        while inizio <= al:
            fine = min(inizio + timedelta(days=BLOCCO_HRV_GG - 1), al)
            try:
                resp = f(inizio, fine)
            except (HttpError, GarminPersonalApiError) as e:
                print(f"  Recupero/{nome}: non disponibile ({type(e).__name__} {getattr(e, 'status_code', '')}).")
                break
            except Exception as e:
                print(f"  Recupero/{nome}: errore ({type(e).__name__}).")
                break
            righe = rec.normalizza(resp)
            if resp and nome not in CAMPI_RECUPERO:
                CAMPI_RECUPERO[nome] = snn.chiavi(resp)        # solo i nomi dei campi, per capire cosa manda Garmin
            if not righe and resp and nome not in diag:
                diag[nome] = snn.chiavi(resp)
            trovate += righe
            inizio = fine + timedelta(days=1)
            time.sleep(PAUSA_S)
        if trovate:
            diag.pop(nome, None)
        liste.append(trovate)
    return rec.unisci(*liste), (diag or None)


def storico_incompleto(attivita: list[dict], t: dict) -> int:
    """Uscite con traccia o analisi ancora da completare nei prossimi giri (0 = storico completo)."""
    n = 0
    for a in attivita:
        k = str(a["id"])
        if k not in t["tracce"]:
            n += t["senza"].get(k, 0) < MAX_TENTATIVI_TRACCIA and perc.da_scaricare(a)
        else:
            n += (t["analisi"].get(k) or {}).get("v") != an.VERSIONE and t["an_falliti"].get(k, 0) < MAX_TENTATIVI_TRACCIA
    return n


def sagome(attivita: list[dict], tracce: dict, per_sport: int = 5, punti: int = 60) -> dict:
    """Forma semplificata delle ultime uscite di ogni sport, in data.enc: l'app la disegna all'apertura
    senza scaricare tutte le tracce (con molti anni di storico tracce.enc è pesante)."""
    out, conta = {}, {}
    for a in attivita:                                         # dalla più recente
        k, sp = str(a["id"]), a.get("sport")
        pts = tracce.get(k)
        if not pts or len(pts) < 2 or conta.get(sp, 0) >= per_sport:
            continue
        scelti = pts[::max(1, len(pts) // punti)]
        if scelti[-1] is not pts[-1]:
            scelti = scelti + [pts[-1]]
        out[k] = [[p[0], p[1]] for p in scelti]
        conta[sp] = conta.get(sp, 0) + 1
    return out


def aggiorna_meteo(attivita: list[dict], tracce: dict, prec_meteo: dict | None) -> dict:
    """Meteo delle uscite con GPS (una richiesta per uscita, una volta sola)."""
    ids_validi = {str(a["id"]) for a in attivita}
    meteo = {k: v for k, v in (prec_meteo or {}).items() if k in ids_validi}
    fatti = errori = 0
    pendenti = sum(1 for a in attivita if str(a["id"]) not in meteo and tracce.get(str(a["id"])))
    limite = MAX_METEO_PER_GIRO if pendenti <= SOGLIA_STORICO else max(MAX_METEO_PER_GIRO, MAX_METEO_STORICO)
    for a in attivita:                                         # dalla più recente
        k = str(a["id"])
        if k in meteo or k not in tracce or not tracce[k]:
            continue
        if fatti >= limite or errori >= 3:
            break
        lat, lon = tracce[k][0][0], tracce[k][0][1]
        try:
            m = mt.scarica(lat, lon, a["inizio"], a.get("durata"))
            fatti += 1
        except Exception:
            errori += 1
            continue
        meteo[k] = m or {}
        time.sleep(0.2)
    print(f"  Meteo: {fatti} uscite aggiornate{', servizio meteo non raggiungibile' if errori >= 3 else ''}.")
    return meteo


def _tempo(a: dict) -> float | None:
    return a.get("mov") or a.get("durata")


def _metrica(a: dict, sport: str) -> float | None:
    """Stessa misura dell'app: passo (s/km) o, nello scialpinismo, VAM in salita (m/h)."""
    if sport == "scialpinismo":
        return ((a.get("an") or {}).get("salita") or {}).get("vam")
    t = _tempo(a)
    return t / (a["dist"] / 1000) if a.get("dist") and t else None


def _mmss(s: float) -> str:
    s = round(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def trova_record(attivita: list[dict], percorsi: list[dict], prec: dict) -> list[str]:
    """Record ottenuti dalle uscite arrivate in questo giro (sui percorsi e sui migliori tempi)."""
    vecchie = {a["id"] for a in prec.get("attivita", [])}
    nuove = [a for a in attivita if a["id"] not in vecchie]
    if not nuove:
        return []
    per_id, msg = {a["id"]: a for a in attivita}, []
    for p in percorsi:
        if p["sport"] == "corsa" and not p.get("manuale"):
            continue
        valori = {i: _metrica(per_id[i], p["sport"]) for i in p["attivita"] if i in per_id}
        valori = {i: v for i, v in valori.items() if v}
        for a in nuove:
            if a["id"] not in valori or len(valori) < 3:
                continue
            altri = [v for i, v in valori.items() if i != a["id"]]
            v = valori[a["id"]]
            if p["sport"] == "scialpinismo" and v > max(altri):
                msg.append(f"Nuovo record su «{p['nome']}»: {round(v)} m/h in salita")
            elif p["sport"] != "scialpinismo" and v < min(altri):
                msg.append(f"Nuovo record su «{p['nome']}»: {_mmss(v)} /km")
    nomi = {"1000": "sul km", "5000": "sui 5 km", "10000": "sui 10 km", "21097": "sulla mezza maratona"}
    ids_nuove = {a["id"] for a in nuove}
    for d, nome in nomi.items():
        prima = [x["an"]["migliori"][d] for x in attivita
                 if x["id"] not in ids_nuove and ((x.get("an") or {}).get("migliori") or {}).get(d)]
        for a in nuove:
            v = ((a.get("an") or {}).get("migliori") or {}).get(d)
            if v and prima and v < min(prima):
                msg.append(f"Nuovo record {nome}: {_mmss(v)}")
    return msg


def scrivi_stato(esito: str, messaggio: str | None = None, **altro) -> None:
    """docs/stato.json, in chiaro: orario del controllo, esito e messaggio (mai dati personali)."""
    ora = datetime.now().astimezone().isoformat(timespec="minutes")
    stato = {"controllato": ora, "esito": esito, "messaggio": messaggio}
    if esito == "ok":
        stato["ultimo_ok"] = ora
    stato.update({k: v for k, v in altro.items() if v is not None})
    STATO_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATO_FILE.write_text(json.dumps(stato, ensure_ascii=False), "utf-8")


def stato_precedente() -> dict:
    try:
        return json.loads(STATO_FILE.read_text("utf-8"))
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def env_obbligatoria(nome: str) -> str:
    v = os.environ.get(nome, "").strip()
    if not v:
        sys.exit(f"ERRORE: variabile d'ambiente {nome} mancante.")
    return v


class ErroreGarmin(Exception):
    """chiave=True: Garmin rifiuta la chiave (serve una chiave nuova); False: servizio non disponibile per ora."""
    def __init__(self, messaggio: str, chiave: bool):
        super().__init__(messaggio)
        self.chiave = chiave


def connetti() -> GarminPersonalApiClient:
    api_key = env_obbligatoria("GARMIN_PERSONAL_API_KEY")
    try:
        token = PersonalApiAuthenticator().obtain_token(api_key)
    except HttpError as e:
        if e.status_code in (400, 401, 403):
            raise ErroreGarmin(f"Garmin ha rifiutato la chiave (HTTP {e.status_code}): è scaduta o è stata eliminata? "
                               "Creane una nuova su Garmin Connect e aggiorna il secret GARMIN_PERSONAL_API_KEY "
                               "su GitHub.", chiave=True) from None
        raise ErroreGarmin(f"Garmin non risponde (HTTP {e.status_code}): nuovo tentativo al prossimo giro.",
                           chiave=False) from None
    except GarminPersonalApiError as e:
        raise ErroreGarmin(f"Risposta inattesa da Garmin ({type(e).__name__}): nuovo tentativo al prossimo giro.",
                           chiave=False) from None
    return GarminPersonalApiClient(token.access_token)


def esplora() -> None:
    if os.environ.get("GITHUB_ACTIONS"):
        sys.exit("--esplora va usato solo sul tuo PC: i log di GitHub Actions sono pubblici.")
    cartella = ROOT / "esplora"
    cartella.mkdir(exist_ok=True)
    oggi = date.today()
    sett_fa = oggi - timedelta(days=7)
    with connetti() as c:
        prove = {
            "attivita_ultime5": lambda: c.search_activities(limit=5),
            "tipi_attivita": c.get_activity_types,
            "hrv_7gg": lambda: c.get_daily_hrv_summaries(sett_fa, oggi),
            "sonno_7gg": lambda: c.get_daily_sleep_summaries(sett_fa, oggi),
            "riepiloghi_giornalieri_7gg": lambda: c.get_daily_summaries(sett_fa, oggi),
            "vo2max_90gg": lambda: c.get_daily_vo2_max(oggi - timedelta(days=90), oggi),
        }
        for nome, f in prove.items():
            try:
                (cartella / f"{nome}.json").write_text(json.dumps(f(), indent=2, ensure_ascii=False), "utf-8")
                print(f"  ok  {nome}.json")
            except Exception as e:
                print(f"  --  {nome}: {type(e).__name__} {getattr(e, 'status_code', '')}")
            time.sleep(PAUSA_S)
    print(f"Fatto. File in {cartella} (non vengono mai caricati su GitHub).")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--completo", action="store_true", help="riscarica tutto lo storico")
    p.add_argument("--esplora", action="store_true", help="salva risposte grezze in ./esplora (solo PC)")
    args = p.parse_args()

    if args.esplora:
        return esplora()

    password = env_obbligatoria("DASHBOARD_PASSWORD")
    notif = nt.config(lambda: leggi_precedente(password, NOTIFICHE_FILE))
    if os.environ.get("PROVA_NOTIFICA", "").lower() == "true":
        if notif:
            ok = nt.invia(notif, "Notifiche attive ✓", "Riceverai un avviso per le nuove corse da classificare e per i record.",
                          tag="prova")
            print(f"  Notifica di prova: {'inviata' if ok else 'non inviata'}.")
        else:
            print("  Notifica di prova: notifiche non attivate nell'app.")
    try:
        aggiorna(password, args, notif)
    except ErroreGarmin as e:
        registra_errore(str(e), e.chiave, notif)
        sys.exit(f"ERRORE: {e}")
    except Exception as e:                                       # rete, servizio Garmin, formato cambiato...
        registra_errore(f"Aggiornamento non riuscito ({type(e).__name__}): nuovo tentativo al prossimo giro.", False, notif)
        raise


def registra_errore(messaggio: str, chiave: bool, notif: dict | None) -> None:
    """Scrive l'esito per l'app e avvisa sul telefono una volta sola: subito se la chiave non vale più,
    dopo 24 ore senza aggiornamenti riusciti se il problema è di Garmin (o della rete)."""
    prima = stato_precedente()
    ultimo_ok = prima.get("ultimo_ok")
    avvisato = bool(prima.get("avvisato")) and prima.get("esito") == "errore"
    fermo_da_ore = None
    try:
        fermo_da_ore = (datetime.now().astimezone() - datetime.fromisoformat(ultimo_ok)).total_seconds() / 3600
    except (TypeError, ValueError):
        pass
    avvisa = notif is not None and not avvisato and (chiave or (fermo_da_ore is not None and fermo_da_ore >= 24))
    if avvisa:
        if chiave:
            avvisa = nt.invia(notif, "Aggiornamento fermo", "La chiave Garmin non funziona più: creane una nuova su Garmin "
                              "Connect e aggiorna il secret GARMIN_PERSONAL_API_KEY su GitHub.", tag="errore")
        else:
            avvisa = nt.invia(notif, "Aggiornamento fermo da un giorno", "Garmin non risponde o qualcosa è cambiato: "
                              "apri l'app per i dettagli.", tag="errore")
    scrivi_stato("errore", messaggio, tipo="chiave" if chiave else "servizio", ultimo_ok=ultimo_ok,
                 avvisato=True if (avvisato or avvisa) else None)


def aggiorna(password: str, args, notif: dict | None) -> None:
    prec = leggi_precedente(password)
    prec_tracce = leggi_precedente(password, TRACCE_FILE)       # le tracce già scaricate non si riscaricano mai
    oggi = date.today()

    with connetti() as client:
        # l'elenco attività si riscarica sempre per intero (poche richieste da 100 attività):
        # così rinomine (#percorso) e cancellazioni vengono sempre recepite
        attivita = scarica_attivita(client, None)
        if prec and not args.completo:
            hrv_dal = oggi - timedelta(days=BLOCCO_HRV_GG)
            hrv_nuovo = scarica_hrv(client, hrv_dal, oggi)
            hrv_vecchio = [h for h in prec.get("hrv", []) if h["data"] < hrv_dal.isoformat()]
            hrv = hrv_vecchio + hrv_nuovo if hrv_nuovo else prec.get("hrv", [])
        else:
            hrv = scarica_hrv(client, oggi - timedelta(days=STORICO_HRV_GG), oggi)

        if prec and prec.get("sonno") and not args.completo:
            s_dal = oggi - timedelta(days=BLOCCO_HRV_GG)
            s_nuovo, sonno_diag = scarica_sonno(client, s_dal, oggi)
            sonno = [x for x in prec["sonno"] if x["data"] < s_dal.isoformat()] + s_nuovo if s_nuovo else prec["sonno"]
        else:
            sonno, sonno_diag = scarica_sonno(client, oggi - timedelta(days=STORICO_HRV_GG), oggi)
        sonno = sorted({x["data"]: x for x in sonno}.values(), key=lambda x: x["data"])

        if prec and prec.get("recupero") and not args.completo:
            r_dal = oggi - timedelta(days=BLOCCO_HRV_GG)
            r_nuovo, recupero_diag = scarica_recupero(client, r_dal, oggi)
            recupero = rec.unisci(r_nuovo, [x for x in prec["recupero"] if x["data"] < r_dal.isoformat()]) if r_nuovo else prec["recupero"]
        else:
            recupero, recupero_diag = scarica_recupero(client, oggi - timedelta(days=STORICO_HRV_GG), oggi)
        recupero = [{k: v for k, v in r.items()} for r in recupero]
        fc_giorni = aggiorna_fc_riposo(client, recupero, (prec or {}).get("fc_giorni"), oggi)
        recupero = unisci_fc(recupero, fc_giorni)

        # deduplica per id, ordina dalla più recente
        attivita = sorted({a["id"]: a for a in attivita}.values(), key=lambda a: a["inizio"], reverse=True)
        hrv = sorted({h["data"]: h for h in hrv}.values(), key=lambda h: h["data"])
        tracce = aggiorna_tracce(client, attivita, prec_tracce)

    meteo = aggiorna_meteo(attivita, tracce["tracce"], (prec or {}).get("meteo"))
    abb = leggi_precedente(password, ABBINAMENTI_FILE) or {}
    percorsi, catturate = perc.riconosci(attivita, tracce["tracce"], abb)
    percorsi, catturate, da_classificare = perc.classifica(attivita, tracce["tracce"], percorsi, abb, catturate)
    con_traccia = set(tracce["tracce"])
    for a in attivita:
        k = str(a["id"])
        a["gps"] = k in con_traccia
        ris = tracce["analisi"].get(k)
        if ris:
            a["an"] = {c: ris[c] for c in ("parziali", "migliori", "gap", "salita") if ris.get(c)}
        if meteo.get(k):
            a["meteo"] = meteo[k]

    # notifiche: nuove corse da classificare e record (mai al primo giro, per non notificare lo storico)
    notificate = set((prec or {}).get("notificate", []))
    if prec is not None and notif:
        nuove = [x for x in da_classificare if x["id"] not in notificate]
        if nuove:
            per_id = {a["id"]: a for a in attivita}
            # come nell'app: il suggerimento vale solo se l'uscita simile è in uno dei TUOI percorsi di corsa
            simili = {i: p["nome"] for p in percorsi if p["sport"] == "corsa" and p.get("manuale") for i in p["attivita"]}
            if len(nuove) == 1:
                a, x = per_id[nuove[0]["id"]], nuove[0]
                dove = f"sembra «{simili[x['simile_a']]}»" if x.get("simile_a") in simili else "nessun percorso simile"
                testo = f"{(a.get('dist') or 0) / 1000:.1f} km · {dove}. Tocca per confermare.".replace(".", ",", 1)
                titolo = "Nuova corsa da classificare"
            else:
                titolo, testo = f"{len(nuove)} nuove corse da classificare", "Tocca per abbinarle ai tuoi percorsi."
            nt.invia(notif, titolo, testo, url="./?apri=inbox", badge=len(da_classificare), tag="classifica")
        # finché lo storico non è completo un "record" potrebbe battere solo una parte delle uscite: nessun avviso
        pendenti = storico_incompleto(attivita, tracce)
        if pendenti:
            print(f"  Record: avvisi in pausa finché lo storico non è completo ({pendenti} uscite da completare).")
        else:
            for m in trova_record(attivita, percorsi, prec)[:3]:
                nt.invia(notif, "Nuovo record 🏆", m, url="./", tag="record")
    notificate = sorted(notificate | {x["id"] for x in da_classificare})[-300:]

    cambiati = []
    if scrivi_se_cambiato(OUT_FILE, {"attivita": attivita, "hrv": hrv, "percorsi": percorsi, "catturate": catturate,
                                     "da_classificare": da_classificare, "sonno": sonno, "sonno_diag": sonno_diag,
                                     "recupero": recupero, "recupero_diag": recupero_diag, "fc_giorni": fc_giorni,
                                     "recupero_campi": CAMPI_RECUPERO or (prec or {}).get("recupero_campi"), "meteo": meteo,
                                     "sagome": sagome(attivita, tracce["tracce"]),
                                     "notificate": notificate}, prec, password):
        cambiati.append("data.enc")
    if scrivi_se_cambiato(TRACCE_FILE, tracce, prec_tracce, password):
        cambiati.append("tracce.enc")
    scrivi_stato("ok")
    if sonno_diag:
        print(f"  Sonno: formato non riconosciuto, {len(sonno_diag)} nomi di campo salvati per la diagnostica.")
    if recupero_diag:
        print(f"  Recupero: formato non riconosciuto per {', '.join(recupero_diag)}; nomi dei campi salvati per la diagnostica.")
    print(f"{len(attivita)} attività, {len(hrv)} giorni HRV, {len(sonno)} notti, {len(recupero)} giorni di recupero, "
          f"{len(percorsi)} percorsi ricorrenti. File aggiornati: {', '.join(cambiati) or 'nessuno'}.")


if __name__ == "__main__":
    main()
