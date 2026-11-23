"""
Tracce GPS (dai file .FIT originali) e riconoscimento dei percorsi ricorrenti.

Regole per i percorsi:
  - Due uscite sono lo stesso percorso se: stessa famiglia di sport, partenza
    entro 300 m, arrivo entro 300 m, distanza entro ±12 % e tracciato medio
    entro 150 m (confronto su 40 punti equidistanti). Il verso conta.
  - Nome manuale: rinomina un'uscita in Garmin Connect iniziando con "#",
    es. "#Giro Zugna". Il percorso riconosciuto che la contiene prende quel nome;
    due percorsi con uscite rinominate con lo stesso #nome vengono uniti.
  - "#escludi" all'inizio del nome: l'uscita non entra in nessun percorso.
"""
from __future__ import annotations

import io
import math
import zipfile
from datetime import date, timedelta

import fitdecode

import analisi as an

SEMICERCHI = 180 / 2**31
PUNTI_FIRMA = 40
TOLL_PARTENZA_M = 300
TOLL_DISTANZA = 0.12
TOLL_TRACCIATO_M = 150
MAX_PUNTI = 500
FAMIGLIE_PERCORSO = {"corsa", "scialpinismo", "trekking"}   # lo sci in pista non ha "percorsi"
TIPI_SENZA_GPS = ("treadmill", "indoor", "virtual", "strength", "cardio", "yoga", "pilates", "elliptical")


# --------------------------------------------------------------------------- #
# Geometria
# --------------------------------------------------------------------------- #
def haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _rdp(xy: list[tuple[float, float]], toll: float) -> list[int]:
    """Douglas-Peucker iterativo: indici dei punti da tenere."""
    n = len(xy)
    if n < 3:
        return list(range(n))
    tieni = [False] * n
    tieni[0] = tieni[-1] = True
    pila = [(0, n - 1)]
    while pila:
        i, j = pila.pop()
        (x1, y1), (x2, y2) = xy[i], xy[j]
        dx, dy = x2 - x1, y2 - y1
        l2 = dx * dx + dy * dy
        dmax, kmax = -1.0, -1
        for k in range(i + 1, j):
            px, py = xy[k]
            if l2 == 0:
                d = math.hypot(px - x1, py - y1)
            else:
                t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
                d = math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
            if d > dmax:
                dmax, kmax = d, k
        if dmax > toll and kmax > 0:
            tieni[kmax] = True
            pila += [(i, kmax), (kmax, j)]
    return [k for k in range(n) if tieni[k]]


def semplifica(punti: list[list]) -> list[list]:
    """Riduce la traccia mantenendone la forma (tolleranza crescente fino a MAX_PUNTI)."""
    if len(punti) <= MAX_PUNTI:
        return punti
    lat0 = math.radians(punti[0][0])
    xy = [(p[1] * 111_320 * math.cos(lat0), p[0] * 110_540) for p in punti]
    toll = 4.0
    while True:
        idx = _rdp(xy, toll)
        if len(idx) <= MAX_PUNTI or toll > 200:
            break
        toll *= 1.6
    if len(idx) > MAX_PUNTI:
        passo = len(idx) / MAX_PUNTI
        idx = [idx[int(i * passo)] for i in range(MAX_PUNTI - 1)] + [idx[-1]]
    return [punti[i] for i in idx]


# --------------------------------------------------------------------------- #
# Lettura FIT
# --------------------------------------------------------------------------- #
def _fit_bytes(contenuto: bytes) -> bytes | None:
    try:
        with zipfile.ZipFile(io.BytesIO(contenuto)) as z:
            nomi = [n for n in z.namelist() if n.lower().endswith(".fit")]
            return z.read(nomi[0]) if nomi else None
    except zipfile.BadZipFile:
        return contenuto if contenuto[8:12] == b".FIT" else None


def estrai_traccia(contenuto: bytes) -> list[list] | None:
    """
    Restituisce i punti [lat, lon, quota_m, distanza_m, secondi_da_inizio, fc] semplificati
    oppure None se il file non ha posizioni GPS (es. tapis roulant).
    """
    punti = punti_completi(contenuto)
    return semplifica(arrotonda(punti)) if punti else None


def estrai(contenuto: bytes, sport: str) -> tuple[list[list] | None, dict | None]:
    """Traccia semplificata per mappe e confronti + analisi sulla traccia completa (parziali, VAM, migliori tempi)."""
    punti = punti_completi(contenuto)
    if not punti:
        return None, None
    try:
        analisi = an.analizza(punti, sport)
    except Exception:                                         # l'analisi non deve mai bloccare la traccia
        analisi = None
    return semplifica(arrotonda(punti)), analisi


def arrotonda(punti: list[list]) -> list[list]:
    return [[round(p[0], 5), round(p[1], 5), round(p[2]) if isinstance(p[2], (int, float)) else None,
             round(p[3]), round(p[4]), int(p[5]) if isinstance(p[5], (int, float)) else None] for p in punti]


def punti_completi(contenuto: bytes) -> list[list] | None:
    """Tutti i punti del file, senza arrotondamenti: [lat, lon, quota, distanza, secondi, fc]."""
    dati = _fit_bytes(contenuto)
    if not dati:
        return None
    grezzi = []
    with fitdecode.FitReader(io.BytesIO(dati), check_crc=fitdecode.CrcCheck.DISABLED) as fr:
        for fr_ in fr:
            if not (isinstance(fr_, fitdecode.FitDataMessage) and fr_.name == "record"):
                continue
            lat = fr_.get_value("position_lat", fallback=None)
            lon = fr_.get_value("position_long", fallback=None)
            ts = fr_.get_value("timestamp", fallback=None)
            if lat is None or lon is None or ts is None:
                continue
            alt = fr_.get_value("enhanced_altitude", fallback=None)
            if alt is None:
                alt = fr_.get_value("altitude", fallback=None)
            grezzi.append((lat * SEMICERCHI, lon * SEMICERCHI, alt,
                           fr_.get_value("distance", fallback=None), ts,
                           fr_.get_value("heart_rate", fallback=None)))
    if len(grezzi) < 2:
        return None

    t0 = grezzi[0][4]
    punti, cum = [], 0.0
    for i, (lat, lon, alt, dist, ts, fc) in enumerate(grezzi):
        if i:
            cum += haversine(grezzi[i - 1][0], grezzi[i - 1][1], lat, lon)
        d = dist if isinstance(dist, (int, float)) else cum
        punti.append([lat, lon, float(alt) if isinstance(alt, (int, float)) else None,
                      float(d), (ts - t0).total_seconds(), fc if isinstance(fc, (int, float)) else None])
    return punti


# --------------------------------------------------------------------------- #
# Riconoscimento percorsi
# --------------------------------------------------------------------------- #
def _firma(punti: list[list], n: int = PUNTI_FIRMA) -> list[tuple[float, float]]:
    """n punti equidistanti lungo la traccia (per distanza percorsa)."""
    cum = [0.0]
    for a, b in zip(punti, punti[1:]):
        cum.append(cum[-1] + haversine(a[0], a[1], b[0], b[1]))
    tot = cum[-1] or 1.0
    out, j = [], 0
    for i in range(n):
        obiettivo = tot * i / (n - 1)
        while j < len(cum) - 2 and cum[j + 1] < obiettivo:
            j += 1
        seg = cum[j + 1] - cum[j] or 1.0
        f = max(0.0, min(1.0, (obiettivo - cum[j]) / seg))
        a, b = punti[j], punti[min(j + 1, len(punti) - 1)]
        out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return out


def _uguali(a: dict, b: dict) -> bool:
    if a["sport"] != b["sport"]:
        return False
    da, db = a["dist"] or 0, b["dist"] or 0
    if not da or not db or abs(da - db) / max(da, db) > TOLL_DISTANZA:
        return False
    fa, fb = a["_firma"], b["_firma"]
    if haversine(*fa[0], *fb[0]) > TOLL_PARTENZA_M or haversine(*fa[-1], *fb[-1]) > TOLL_PARTENZA_M:
        return False
    media = sum(haversine(*p, *q) for p, q in zip(fa, fb)) / len(fa)
    return media <= TOLL_TRACCIATO_M


def _nome_manuale(nome: str) -> str | None:
    s = (nome or "").strip()
    return s[1:].strip() if s.startswith("#") and len(s) > 1 else None


def normalizza_abbinamenti(abb: dict | None) -> dict:
    """Formato v2: ancore (attività che 'rappresenta' il suo gruppo automatico), assegna (singole uscite spostate)."""
    abb = dict(abb or {})
    if abb.get("v", 1) < 2:                     # v1: ogni assegnazione era un'ancora
        abb = {"v": 2, "gruppi": abb.get("gruppi") or {}, "ancore": abb.get("assegna") or {},
               "assegna": {}, "escludi": abb.get("escludi") or []}
    for k in ("gruppi", "ancore", "assegna"):
        abb[k] = {str(a): b for a, b in (abb.get(k) or {}).items()}
    abb["escludi"] = [str(x) for x in (abb.get("escludi") or [])]
    return abb


def riconosci_percorsi(attivita: list[dict], tracce: dict[str, list], abb: dict | None = None) -> list[dict]:
    return riconosci(attivita, tracce, abb)[0]


def riconosci(attivita: list[dict], tracce: dict[str, list], abb: dict | None = None) -> tuple[list[dict], dict]:
    """
    1) raggruppa in automatico le uscite con tracciato uguale (escluse quelle spostate a mano);
    2) unisce i gruppi che condividono una chiave: ancora dell'app (rinomina/unisci) o #nome in Garmin;
    3) aggiunge le uscite spostate a mano nel gruppo scelto (abb["assegna"]);
    4) toglie le uscite escluse (abb["escludi"] o "#escludi");
    5) le NUOVE uscite (successive alla creazione del percorso) con tracciato uguale a una delle
       uscite messe a mano in un percorso vengono abbinate lì in automatico ("catturate").
    Restituisce (percorsi, catturate {id attività: chiave gruppo}).
    """
    abb = normalizza_abbinamenti(abb)
    ancore, assegna, nomi_app = abb["ancore"], abb["assegna"], {k: (g or {}).get("nome") for k, g in abb["gruppi"].items()}
    escludi = set(abb["escludi"])

    cand = []
    for a in sorted(attivita, key=lambda x: x["inizio"]):           # dalla più vecchia
        sid = str(a["id"])
        manuale = _nome_manuale(a.get("nome", ""))
        if sid in assegna:
            pts = tracce.get(sid)
            cand.append({**a, "_chiave": f"app:{assegna[sid]}", "_app": assegna[sid], "_manuale": None, "_firma": None,
                         "_firma_m": _firma(pts) if pts and len(pts) >= 10 else None, "_spostata": True})
            continue
        if sid in escludi or (manuale and manuale.lower().startswith("escludi")):
            continue
        if a["sport"] not in FAMIGLIE_PERCORSO and not manuale and sid not in ancore:
            continue
        k_anc = ancore.get(sid)
        chiave = f"app:{k_anc}" if k_anc else (f"#:{manuale.casefold()}" if manuale else None)
        pts = tracce.get(sid)
        cand.append({**a, "_chiave": chiave, "_app": k_anc, "_manuale": None if k_anc else manuale,
                     "_firma": _firma(pts) if pts and len(pts) >= 10 else None, "_spostata": False})

    # 5) cattura delle nuove uscite nei percorsi composti a mano
    modelli: dict[str, list[dict]] = {}
    for a in cand:
        if a["_spostata"]:
            modelli.setdefault(a["_app"], []).append(a)
    dal = {}
    for k, membri in modelli.items():
        dal[k] = (abb["gruppi"].get(k) or {}).get("dal") or max(m["inizio"] for m in membri)
    catturate: dict[str, str] = {}
    for a in cand:
        if a["_spostata"] or a["_app"] or a["_manuale"] or not a["_firma"]:
            continue
        for k, membri in modelli.items():
            if a["inizio"] > dal[k] and any(m["_firma_m"] and _uguali(a, {**m, "_firma": m["_firma_m"]}) for m in membri):
                a.update(_chiave=f"app:{k}", _app=k, _spostata=True)
                catturate[str(a["id"])] = k
                break

    # 1) gruppi automatici (le uscite spostate a mano restano singole)
    gruppi: list[list[dict]] = []
    for a in cand:
        if a["_firma"] and not a["_spostata"]:
            for g in gruppi:
                if g[0]["_firma"] and not g[0]["_spostata"] and _uguali(a, g[0]):
                    g.append(a)
                    break
            else:
                gruppi.append([a])
        else:
            gruppi.append([a])

    # 2-3) unione per chiave (union-find)
    padre = list(range(len(gruppi)))
    def radice(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i
    primo: dict[str, int] = {}
    for i, g in enumerate(gruppi):
        for a in g:
            if a["_chiave"]:
                if a["_chiave"] in primo:
                    padre[radice(i)] = radice(primo[a["_chiave"]])
                else:
                    primo[a["_chiave"]] = i
    uniti: dict[int, list[dict]] = {}
    for i, g in enumerate(gruppi):
        uniti.setdefault(radice(i), []).extend(g)

    percorsi = []
    for membri in uniti.values():
        membri.sort(key=lambda x: x["inizio"])
        chiavi_app = [m["_app"] for m in membri if m["_app"]]
        nomi_hash = [m["_manuale"] for m in membri if m["_manuale"]]
        if len(membri) < 2 and not chiavi_app and not nomi_hash:
            continue
        rif = next((m for m in membri if m["_firma"]), next((m for m in membri if not m["_spostata"]), membri[0]))
        nome = next((nomi_app.get(k) for k in chiavi_app if nomi_app.get(k)), None) \
            or (nomi_hash[-1] if nomi_hash else None)
        if not nome:
            km = (rif["dist"] or 0) / 1000
            nome = f"{rif.get('luogo') or 'Percorso'} · {km:.1f} km".replace(".", ",")
        percorsi.append({
            "id": rif["id"], "nome": nome, "manuale": bool(chiavi_app or nomi_hash), "sport": rif["sport"],
            "rif": rif["id"], "attivita": [m["id"] for m in membri], "chiave": chiavi_app[0] if chiavi_app else None,
        })
    percorsi.sort(key=lambda p: (-len(p["attivita"]), p["nome"]))
    return percorsi, catturate


GIORNI_DA_CLASSIFICARE = 14
MEMBRI_CONFRONTO = 25


def classifica(attivita: list[dict], tracce: dict[str, list], percorsi: list[dict], abb: dict | None,
               catturate: dict, oggi: date | None = None) -> tuple[list[dict], dict, list[dict]]:
    """
    Solo corsa. Per ogni uscita non ancora in un percorso "tuo" (cluster):
      - se il tracciato è uguale (tolleranze standard) a quello di un solo cluster → abbinata in automatico;
      - altrimenti, se è recente (ultimi GIORNI_DA_CLASSIFICARE giorni) → va in "da classificare",
        con il suggerimento del cluster più simile ("simile_a" = id di un'uscita di quel cluster).
    Le scelte esplicite (spostate, escluse, #nome, ancore) non vengono mai toccate.
    """
    abb = normalizza_abbinamenti(abb)
    escludi, assegna, ancore = set(abb["escludi"]), abb["assegna"], abb["ancore"]
    byid = {a["id"]: a for a in attivita}
    cache: dict = {}
    def F(i):
        if i not in cache:
            pts = tracce.get(str(i))
            cache[i] = _firma(pts) if pts and len(pts) >= 10 else None
        return cache[i]

    clusters = [p for p in percorsi if p["sport"] == "corsa" and p["manuale"]]
    in_cluster = {i for p in clusters for i in p["attivita"]}
    limite = ((oggi or date.today()) - timedelta(days=GIORNI_DA_CLASSIFICARE)).isoformat()
    da_classificare = []

    for a in sorted(attivita, key=lambda x: x["inizio"]):
        sid = str(a["id"])
        if a["sport"] != "corsa" or a["id"] in in_cluster or sid in escludi or sid in assegna or sid in ancore \
                or _nome_manuale(a.get("nome", "")):
            continue
        fa = F(a["id"])
        if not fa:
            continue
        recente = a["inizio"][:10] >= limite
        aa = {**a, "_firma": fa}
        uguali, migliore = [], None
        for p in clusters:
            dal = (abb["gruppi"].get(p["chiave"] or "") or {}).get("dal")
            if not recente and not (dal and a["inizio"] > dal):
                continue
            membri = [m for m in p["attivita"] if m != a["id"] and F(m)][-MEMBRI_CONFRONTO:]
            if any(_uguali(aa, {**byid[m], "_firma": F(m)}) for m in membri if m in byid):
                uguali.append(p)
            for m in membri:
                if m not in byid:
                    continue
                fm, dm, da_ = F(m), byid[m].get("dist") or 0, a.get("dist") or 0
                media = sum(haversine(*x, *y) for x, y in zip(fa, fm)) / len(fa)
                scarto = abs(da_ - dm) / max(da_, dm) if da_ and dm else 1
                punteggio = media + scarto * 2000            # metri "equivalenti"
                if migliore is None or punteggio < migliore[0]:
                    migliore = (punteggio, m, media)
        if len(uguali) == 1 and uguali[0]["chiave"]:
            p = uguali[0]
            for q in percorsi:                               # la tolgo dal gruppo automatico in cui era
                if a["id"] in q["attivita"]:
                    q["attivita"].remove(a["id"])
            p["attivita"].append(a["id"])
            p["attivita"].sort(key=lambda i: byid[i]["inizio"])
            catturate[sid] = p["chiave"]
            in_cluster.add(a["id"])
        elif recente:
            da_classificare.append({
                "id": a["id"],
                "simile_a": migliore[1] if migliore and migliore[2] < 1500 else None,
                "distanza_m": round(migliore[2]) if migliore else None,
            })

    # pulizia: gruppi automatici rimasti con meno di 2 uscite; riferimento sempre valido
    out = []
    for p in percorsi:
        if not p["attivita"] or (len(p["attivita"]) < 2 and not p["manuale"]):
            continue
        if p["rif"] not in p["attivita"]:
            p["rif"] = p["id"] = p["attivita"][0]
        out.append(p)
    out.sort(key=lambda p: (-len(p["attivita"]), p["nome"]))
    return out, catturate, da_classificare


def da_scaricare(a: dict) -> bool:
    tipo = (a.get("tipo") or "").lower()
    return not any(t in tipo for t in TIPI_SENZA_GPS)
