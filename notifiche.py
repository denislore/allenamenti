"""
Notifiche sul telefono (Web Push) inviate da GitHub Actions dopo ogni aggiornamento.

Il file docs/notifiche.enc è scritto dall'app quando tocchi «Attiva notifiche» ed è cifrato con la password
della dashboard. Contiene la sottoscrizione del telefono e la chiave VAPID (generata sul telefono): nessun
secret aggiuntivo da configurare su GitHub. Lo script non stampa mai contenuti, solo esiti.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit


def config(leggi) -> dict | None:
    """leggi: funzione che decifra docs/notifiche.enc (o restituisce None)."""
    c = leggi()
    if not c or not c.get("attive") or not c.get("sottoscrizione") or not c.get("vapid"):
        return None
    return c


def _sub(c: dict) -> str:
    """Contatto VAPID richiesto da Apple: l'indirizzo https del sito della dashboard, solo il dominio
    (py_vapid rifiuta un indirizzo con percorso, es. «/allenamenti/»), oppure un indirizzo mailto."""
    u = urlsplit(str(c.get("sito", "")))
    if u.scheme == "https" and u.hostname:
        return f"https://{u.hostname}"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        return f"https://{repo.split('/', 1)[0].lower()}.github.io"
    return "mailto:allenamenti@example.org"


def invia(c: dict, titolo: str, testo: str, url: str = "./", badge: int | None = None, tag: str | None = None,
          invio=None) -> bool:
    """Invia una notifica; restituisce True se il servizio push l'ha accettata."""
    from py_vapid import Vapid02
    from pywebpush import WebPushException, webpush
    payload = {"title": titolo, "body": testo, "url": url}
    if badge is not None:
        payload["badge"] = badge
    if tag:
        payload["tag"] = tag
    try:
        (invio or webpush)(subscription_info=c["sottoscrizione"], data=json.dumps(payload, ensure_ascii=False),
                           vapid_private_key=Vapid02.from_der(c["vapid"].encode()),
                           vapid_claims={"sub": _sub(c)}, ttl=86400, headers={"Urgency": "normal"}, timeout=20)
        return True
    except WebPushException as e:
        stato = getattr(getattr(e, "response", None), "status_code", "?")
        print(f"  Notifica non inviata (servizio push: {stato}).")
        if stato in (404, 410):
            print("  La sottoscrizione del telefono non è più valida: riattiva le notifiche dall'app.")
        return False
    except Exception as e:                                        # la notifica non deve mai bloccare l'aggiornamento
        print(f"  Notifica non inviata ({type(e).__name__}).")
        return False
