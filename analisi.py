"""
Analisi di un'uscita dal file .FIT a piena risoluzione (prima che la traccia venga semplificata).

Per ogni uscita calcola:
  - parziali al km: tempo in movimento, FC media, dislivello positivo e negativo, passo equivalente in piano;
  - migliori tempi su 1 km, 5 km, 10 km e mezza maratona (tempo in movimento, solo corsa);
  - passo equivalente in piano dell'intera uscita (GAP), stimato con il modello di Minetti (2002)
    sul costo energetico della corsa in pendenza: in salita è più veloce del passo reale, in discesa più lento;
  - salita: VAM (metri di dislivello all'ora contando solo i tratti in salita), tempo e dislivello in salita
    e in discesa, FC media in salita. Serve soprattutto per scialpinismo e trekking.

Le pause (soste, cambi assetto) non contano come tempo in movimento.
"""
from __future__ import annotations

VERSIONE = 1
DISTANZE_MIGLIORI = (1000, 5000, 10000, 21097)
V_MIN = 0.3            # m/s: sotto questa velocità orizzontale…
VZ_MIN = 0.02          # m/s: …e sotto questa velocità verticale (72 m/h) si è fermi
PAUSA_S = 30           # un intervallo lungo senza spostamento è una pausa
FINESTRA_PENDENZA_M = 25
FINESTRA_VERTICALE_S = 30
VZ_SALITA = 0.03       # m/s (108 m/h): sopra si è in salita
VZ_DISCESA = -0.05     # m/s (180 m/h): sotto si è in discesa


def costo_minetti(g: float) -> float:
    """Costo energetico della corsa (J/kg/m) in funzione della pendenza (frazione). In piano 3,6."""
    g = max(-0.45, min(0.45, g))
    return 155.4 * g ** 5 - 30.4 * g ** 4 - 43.3 * g ** 3 + 46.3 * g ** 2 + 19.5 * g + 3.6


def _liscia(valori: list, mezza: int) -> list:
    """Media mobile centrata; i valori mancanti vengono ignorati (restano None se non c'è nulla attorno)."""
    n, out = len(valori), []
    somma, cont = 0.0, 0
    # finestra scorrevole [i-mezza, i+mezza]
    for i in range(min(mezza, n)):
        if valori[i] is not None:
            somma += valori[i]; cont += 1
    for i in range(n):
        dentro = i + mezza
        if dentro < n and valori[dentro] is not None:
            somma += valori[dentro]; cont += 1
        fuori = i - mezza - 1
        if fuori >= 0 and valori[fuori] is not None:
            somma -= valori[fuori]; cont -= 1
        out.append(somma / cont if cont else None)
    return out


def _interp(xs: list, ys: list, x: float, j: int) -> tuple[float, int]:
    """Valore di y in x per interpolazione lineare; j è l'indice da cui cercare (xs crescente). Restituisce (y, j)."""
    n = len(xs)
    while j < n - 1 and xs[j + 1] < x:
        j += 1
    if j >= n - 1:
        return ys[-1], j
    x0, x1 = xs[j], xs[j + 1]
    f = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
    return ys[j] + (ys[j + 1] - ys[j]) * max(0.0, min(1.0, f)), j


def analizza(punti: list[list], sport: str) -> dict | None:
    """punti: [lat, lon, quota, distanza_m, secondi, fc] a piena risoluzione, in ordine di tempo."""
    if not punti or len(punti) < 10:
        return None
    n = len(punti)
    dist = [float(p[3] or 0) for p in punti]
    for i in range(1, n):                                   # la distanza non deve mai tornare indietro
        if dist[i] < dist[i - 1]:
            dist[i] = dist[i - 1]
    tempo = [float(p[4] or 0) for p in punti]
    fc = [p[5] if isinstance(p[5], (int, float)) and p[5] > 0 else None for p in punti]
    quota_grezza = [p[2] if isinstance(p[2], (int, float)) else None for p in punti]
    ha_quota = sum(q is not None for q in quota_grezza) >= n * 0.8
    quota = _liscia(quota_grezza, 5) if ha_quota else [None] * n

    # --- movimento, tempo cumulato in movimento
    mov = [0.0] * n                    # tempo in movimento cumulato
    in_moto = [False] * n              # il tratto (i-1 → i) è in movimento
    for i in range(1, n):
        dt = tempo[i] - tempo[i - 1]
        if dt <= 0:
            mov[i] = mov[i - 1]; continue
        dd = dist[i] - dist[i - 1]
        dz = (quota[i] - quota[i - 1]) if quota[i] is not None and quota[i - 1] is not None else 0.0
        fermo = dd / dt < V_MIN and abs(dz) / dt < VZ_MIN
        pausa = dt > PAUSA_S and dd / dt < V_MIN
        in_moto[i] = not (fermo or pausa)
        mov[i] = mov[i - 1] + (dt if in_moto[i] else 0.0)

    # --- pendenza (su ±25 m) e distanza equivalente in piano
    pend = [0.0] * n
    if ha_quota:
        k = j = 0
        for i in range(n):
            while k < i and dist[i] - dist[k + 1] >= FINESTRA_PENDENZA_M:
                k += 1
            j = max(j, i)
            while j < n - 1 and dist[j] - dist[i] < FINESTRA_PENDENZA_M:
                j += 1
            dd = dist[j] - dist[k]
            if dd >= 10 and quota[j] is not None and quota[k] is not None:
                pend[i] = (quota[j] - quota[k]) / dd
    equiv = [0.0] * n                   # distanza equivalente in piano cumulata (solo tratti in movimento)
    for i in range(1, n):
        dd = dist[i] - dist[i - 1]
        f = costo_minetti((pend[i] + pend[i - 1]) / 2) / 3.6 if ha_quota else 1.0
        equiv[i] = equiv[i - 1] + (dd * f if in_moto[i] else 0.0)

    tot_m, tot_mov = dist[-1], mov[-1]
    out: dict = {"v": VERSIONE}
    if tot_m >= 500 and tot_mov > 0 and equiv[-1] > 0:
        out["gap"] = round(tot_mov / (equiv[-1] / 1000), 1)          # secondi al km equivalenti in piano

    # --- parziali al km: [metri, secondi, fc, d+, d-, secondi equivalenti in piano]
    if tot_m >= 1000 and sport in ("corsa", "trekking", "altro"):
        confini = [0.0] + [1000.0 * k for k in range(1, int(tot_m // 1000) + 1)]
        if tot_m - confini[-1] >= 100:
            confini.append(tot_m)
        parziali, jm, je = [], 0, 0
        t_prec, e_prec = 0.0, 0.0
        acc_fc, acc_dt, su, giu, seg = 0.0, 0.0, 0.0, 0.0, 1
        for a, b in zip(confini, confini[1:]):
            t_b, jm = _interp(dist, mov, b, jm)
            e_b, je = _interp(dist, equiv, b, je)
            acc_fc = acc_dt = su = giu = 0.0
            while seg < n and dist[seg] <= b + 1e-9:
                if in_moto[seg]:
                    dt = tempo[seg] - tempo[seg - 1]
                    if fc[seg] is not None:
                        acc_fc += fc[seg] * dt; acc_dt += dt
                if quota[seg] is not None and quota[seg - 1] is not None:
                    dz = quota[seg] - quota[seg - 1]
                    su += max(0.0, dz); giu += max(0.0, -dz)
                seg += 1
            metri = b - a
            sec = t_b - t_prec
            eq = e_b - e_prec
            gap = sec / (eq / metri) if eq > 0 else None
            parziali.append([round(metri), round(sec), round(acc_fc / acc_dt) if acc_dt else None,
                             round(su) if ha_quota else None, round(giu) if ha_quota else None,
                             round(gap) if gap and ha_quota else None])
            t_prec, e_prec = t_b, e_b
        out["parziali"] = parziali

    # --- migliori tempi (corsa)
    if sport == "corsa":
        migliori = {}
        for d in DISTANZE_MIGLIORI:
            if tot_m < d:
                break
            best, j = None, 0
            for i in range(n):
                if dist[i] + d > tot_m:
                    break
                t_fine, j = _interp(dist, mov, dist[i] + d, max(j, i))
                durata = t_fine - mov[i]
                if durata > 0 and (best is None or durata < best):
                    best = durata
            if best:
                migliori[str(d)] = round(best)
        if migliori:
            out["migliori"] = migliori

    # --- salita e discesa (velocità verticale su ±30 s)
    if ha_quota:
        k = j = 0
        su = sec_su = giu = sec_giu = acc_fc = acc_dt = 0.0
        for i in range(1, n):
            while k < i and tempo[i] - tempo[k + 1] >= FINESTRA_VERTICALE_S:
                k += 1
            j = max(j, i)
            while j < n - 1 and tempo[j] - tempo[i] < FINESTRA_VERTICALE_S:
                j += 1
            if not in_moto[i] or quota[j] is None or quota[k] is None or tempo[j] == tempo[k]:
                continue
            vz = (quota[j] - quota[k]) / (tempo[j] - tempo[k])
            dt = tempo[i] - tempo[i - 1]
            dz = quota[i] - quota[i - 1]
            if vz > VZ_SALITA:
                su += max(0.0, dz); sec_su += dt
                if fc[i] is not None:
                    acc_fc += fc[i] * dt; acc_dt += dt
            elif vz < VZ_DISCESA:
                giu += max(0.0, -dz); sec_giu += dt
        if su >= 50 and sec_su >= 120:
            out["salita"] = {"vam": round(su / sec_su * 3600), "su": round(su), "sec": round(sec_su),
                             "giu": round(giu), "sec_giu": round(sec_giu),
                             "fc": round(acc_fc / acc_dt) if acc_dt else None}
    return out
