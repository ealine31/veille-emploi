#!/usr/bin/env python3
"""
Veille quotidienne - Offres d'emploi analyse sensorielle en Occitanie.
Envoie un récapitulatif par email via Gmail SMTP.

Configuration requise (variables d'environnement) :
  GMAIL_APP_PASSWORD  : Mot de passe d'application Gmail (16 caractères)
  FT_CLIENT_ID        : (optionnel) Client ID API France Travail
  FT_CLIENT_SECRET    : (optionnel) Client Secret API France Travail
"""

import os
import time
import smtplib
import requests
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("/home/user/veille_emploi/veille.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
GMAIL_USER        = "emilie.aline@gmail.com"
TO_EMAIL          = "emilie.aline@gmail.com"
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

FT_CLIENT_ID     = os.environ.get("FT_CLIENT_ID", "")
FT_CLIENT_SECRET = os.environ.get("FT_CLIENT_SECRET", "")

KEYWORDS = [
    "analyse sensorielle",
    "évaluation sensorielle",
    "evaluation sensorielle",
    "caractérisation sensorielle",
    "caracterisation sensorielle",
    "organoleptique",
    "dégustation",
    "degustation",
    "consumer",
]

OCCITANIE_TERMS = [
    "toulouse", "montpellier", "nîmes", "nimes", "perpignan",
    "carcassonne", "albi", "auch", "cahors", "mende", "foix",
    "rodez", "tarbes", "occitanie", "haute-garonne", "hérault",
    "herault", "gard", "aude", "ariège", "ariege", "aveyron",
    "gers", "lot", "lozère", "lozere", "hautes-pyrénées",
    "hautes-pyrenees", "pyrénées-orientales", "pyrenees-orientales",
    "tarn", "tarn-et-garonne", "balma", "labège", "labege",
    "colomiers", "blagnac", "muret", "castres", "alès", "ales",
    "sète", "sete", "beziers", "béziers", "lunel", "millau",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ── Utilitaires ──────────────────────────────────────────────────────────────

def deduplicate(offers: list) -> list:
    seen, unique = set(), []
    for o in offers:
        key = o.get("url") or f"{o.get('title','')}_{o.get('company','')}"
        if key and key not in seen:
            seen.add(key)
            unique.append(o)
    return unique


def contains_keyword(offer: dict) -> bool:
    text = (
        offer.get("title", "") + " " + offer.get("description", "")
    ).lower()
    return any(kw in text for kw in KEYWORDS)


def in_occitanie(offer: dict) -> bool:
    loc = offer.get("location", "").lower()
    if not loc:
        return True  # pas de localisation précisée → on garde
    return any(t in loc for t in OCCITANIE_TERMS)


def safe_get(url: str, params: dict = None, timeout: int = 15) -> requests.Response | None:
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        if r.status_code == 200:
            return r
        log.debug("HTTP %s pour %s", r.status_code, url)
    except Exception as e:
        log.debug("Erreur GET %s : %s", url, e)
    return None


# ── Sources d'offres ─────────────────────────────────────────────────────────

def get_ft_token() -> str | None:
    if not FT_CLIENT_ID or not FT_CLIENT_SECRET:
        return None
    try:
        r = requests.post(
            "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
            "?realm=%2Fpartenaire",
            data={
                "grant_type": "client_credentials",
                "client_id": FT_CLIENT_ID,
                "client_secret": FT_CLIENT_SECRET,
                "scope": "api_offresdemploiv2 o2dsoffre",
            },
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("access_token")
    except Exception as e:
        log.warning("Erreur token FT : %s", e)
    return None


def search_france_travail_api(token: str) -> list:
    """Recherche via l'API officielle France Travail."""
    offers = []
    search_terms = [
        "analyse sensorielle", "évaluation sensorielle",
        "organoleptique", "dégustation", "consumer sensoriel",
    ]
    for term in search_terms:
        try:
            r = requests.get(
                "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                params={"motsCles": term, "lieuTravail.codeInsee": "76", "range": "0-49", "sort": "1"},
                timeout=15,
            )
            if r.status_code == 200:
                for o in r.json().get("resultats", []):
                    place = o.get("lieuTravail", {})
                    offers.append({
                        "title":       o.get("intitule", ""),
                        "company":     o.get("entreprise", {}).get("nom", "Non précisé"),
                        "location":    place.get("libelle", ""),
                        "date":        (o.get("dateCreation") or "")[:10],
                        "url":         f"https://candidat.francetravail.fr/offres/recherche/detail/{o.get('id','')}",
                        "description": o.get("description", "")[:600],
                        "source":      "France Travail",
                    })
            time.sleep(1)
        except Exception as e:
            log.warning("API FT '%s' : %s", term, e)
    return offers


def search_france_travail_scraping() -> list:
    """Fallback scraping France Travail sans token API."""
    offers = []
    terms = ["analyse+sensorielle", "organoleptique", "d%C3%A9gustation", "%C3%A9valuation+sensorielle"]
    for term in terms:
        url = (
            f"https://candidat.francetravail.fr/offres/recherche"
            f"?motsCles={term}&lieux=76R&tri=0"
        )
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for card in soup.select(".result, [data-id-offre]"):
            title_el   = card.select_one("h2, h3, .title")
            company_el = card.select_one(".entreprise-nom, [class*='company']")
            loc_el     = card.select_one(".lieuTravail, [class*='location']")
            link_el    = card.select_one("a[href]")
            href = link_el["href"] if link_el else ""
            if href and not href.startswith("http"):
                href = "https://candidat.francetravail.fr" + href
            if title_el:
                offers.append({
                    "title":       title_el.get_text(strip=True),
                    "company":     company_el.get_text(strip=True) if company_el else "Non précisé",
                    "location":    loc_el.get_text(strip=True) if loc_el else "",
                    "date":        datetime.today().strftime("%Y-%m-%d"),
                    "url":         href,
                    "description": card.get_text(" ", strip=True)[:400],
                    "source":      "France Travail",
                })
        time.sleep(1.5)
    return offers


def search_hellowork() -> list:
    offers = []
    terms = ["analyse+sensorielle", "organoleptique", "%C3%A9valuation+sensorielle", "d%C3%A9gustation"]
    for term in terms:
        url = (
            f"https://www.hellowork.com/fr-fr/emploi/recherche.html"
            f"?k={term}&l=Occitanie&s=created_desc"
        )
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for card in soup.select("article, li[data-id], [class*='offer']"):
            title_el = card.select_one("h2, h3, [class*='title']")
            co_el    = card.select_one("[class*='company'], [class*='employer']")
            loc_el   = card.select_one("[class*='location'], [class*='city']")
            link_el  = card.select_one("a[href]")
            if not title_el:
                continue
            href = link_el["href"] if link_el else ""
            if href and not href.startswith("http"):
                href = "https://www.hellowork.com" + href
            offers.append({
                "title":       title_el.get_text(strip=True),
                "company":     co_el.get_text(strip=True) if co_el else "Non précisé",
                "location":    loc_el.get_text(strip=True) if loc_el else "Occitanie",
                "date":        datetime.today().strftime("%Y-%m-%d"),
                "url":         href,
                "description": card.get_text(" ", strip=True)[:400],
                "source":      "HelloWork",
            })
        time.sleep(1.5)
    return offers


def search_regionsjob() -> list:
    """RegionsJob / Meteojob — plus accessible."""
    offers = []
    terms = ["analyse sensorielle", "organoleptique", "dégustation", "évaluation sensorielle"]
    for term in terms:
        url = "https://www.meteojob.com/jobsearch/search"
        r = safe_get(url, params={"what": term, "where": "Occitanie"})
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for card in soup.select(".job-offer, article"):
            title_el = card.select_one("h2, h3, .offer-title")
            co_el    = card.select_one(".company, .employer")
            loc_el   = card.select_one(".location, .city")
            link_el  = card.select_one("a[href]")
            if not title_el:
                continue
            href = link_el["href"] if link_el else ""
            if href and not href.startswith("http"):
                href = "https://www.meteojob.com" + href
            offers.append({
                "title":       title_el.get_text(strip=True),
                "company":     co_el.get_text(strip=True) if co_el else "Non précisé",
                "location":    loc_el.get_text(strip=True) if loc_el else "Occitanie",
                "date":        datetime.today().strftime("%Y-%m-%d"),
                "url":         href,
                "description": card.get_text(" ", strip=True)[:400],
                "source":      "Meteojob",
            })
        time.sleep(1.5)
    return offers


# ── Email ────────────────────────────────────────────────────────────────────

def build_html(offers: list, date_str: str) -> str:
    if offers:
        cards_html = ""
        for o in offers:
            kw_found = [kw for kw in KEYWORDS if kw in (o.get("title","") + " " + o.get("description","")).lower()]
            badge = ", ".join(dict.fromkeys(kw_found))  # dédoublonné, ordre préservé
            desc = (o.get("description") or "")[:300]
            cards_html += f"""
        <div style="border:1px solid #dce1e7;border-radius:10px;padding:16px;
                    margin:12px 0;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.05);">
          <div style="font-size:.75em;color:#888;margin-bottom:4px;">{o.get('source','')}</div>
          <h3 style="margin:0 0 6px;font-size:1.05em;">
            <a href="{o.get('url','#')}" style="color:#1a73e8;text-decoration:none;">
              {o.get('title','')}
            </a>
          </h3>
          <p style="margin:4px 0;color:#555;font-size:.9em;">
            🏢 <strong>{o.get('company','')}</strong>
            &nbsp;|&nbsp; 📍 {o.get('location','')}
            &nbsp;|&nbsp; 📅 {o.get('date','')}
          </p>
          {f'<p style="margin:4px 0;font-size:.8em;color:#e06c00;">🔑 {badge}</p>' if badge else ''}
          <p style="margin:8px 0 10px;color:#444;font-size:.88em;">{desc}…</p>
          <a href="{o.get('url','#')}"
             style="background:#1a73e8;color:#fff;padding:7px 15px;border-radius:5px;
                    text-decoration:none;font-size:.85em;">
            Voir l'offre →
          </a>
        </div>"""
        content = f"<p><strong>{len(offers)} offre(s) trouvée(s)</strong> aujourd'hui :</p>" + cards_html
    else:
        content = """
        <div style="background:#f8f9fa;border-radius:10px;padding:20px;text-align:center;color:#666;">
          <p style="font-size:1.1em;">Aucune offre trouvée aujourd'hui en Occitanie.</p>
          <p>Les plateformes seront à nouveau consultées demain.</p>
        </div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:'Helvetica Neue',Arial,sans-serif;max-width:760px;
             margin:auto;padding:24px;background:#f4f6f9;color:#333;">
  <div style="background:#fff;border-radius:12px;padding:28px;
              box-shadow:0 2px 8px rgba(0,0,0,.08);">
    <h1 style="color:#1a73e8;margin-top:0;font-size:1.4em;border-bottom:2px solid #e8eaed;padding-bottom:12px;">
      🔬 Veille Emploi — Analyse Sensorielle · Occitanie
    </h1>
    <p style="color:#888;margin-top:0;">Rapport du <strong>{date_str}</strong></p>
    {content}
    <hr style="border:none;border-top:1px solid #e8eaed;margin:24px 0;">
    <p style="color:#aaa;font-size:.78em;line-height:1.6;">
      <strong>Mots-clés surveillés :</strong> analyse sensorielle · évaluation sensorielle ·
      caractérisation sensorielle · organoleptique · dégustations · consumer<br>
      <strong>Sources :</strong> France Travail · HelloWork · Meteojob<br>
      <strong>Région :</strong> Occitanie (tous départements)
    </p>
  </div>
</body></html>"""


def send_email(html: str, subject: str) -> None:
    if not GMAIL_APP_PASSWORD:
        log.error("GMAIL_APP_PASSWORD non défini — email non envoyé.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_USER, TO_EMAIL, msg.as_string())
    log.info("✓ Email envoyé à %s", TO_EMAIL)


# ── Point d'entrée ───────────────────────────────────────────────────────────

def main() -> None:
    today    = datetime.today()
    date_str = today.strftime("%d/%m/%Y")
    subject  = f"[Veille Emploi] Analyse Sensorielle Occitanie — {date_str}"

    log.info("=== Démarrage veille emploi %s ===", date_str)

    all_offers: list = []

    # 1. France Travail (API si credentials, sinon scraping)
    log.info("→ France Travail…")
    token = get_ft_token()
    if token:
        log.info("  Mode API (token OK)")
        all_offers.extend(search_france_travail_api(token))
    else:
        log.info("  Mode scraping (pas de token API)")
        all_offers.extend(search_france_travail_scraping())

    # 2. HelloWork
    log.info("→ HelloWork…")
    all_offers.extend(search_hellowork())

    # 3. Meteojob
    log.info("→ Meteojob…")
    all_offers.extend(search_regionsjob())

    # Filtrage et dédoublonnage
    filtered = deduplicate(
        [o for o in all_offers if contains_keyword(o) and in_occitanie(o)]
    )
    log.info("✓ %d offre(s) après filtrage", len(filtered))

    # Envoi email
    html = build_html(filtered, date_str)
    send_email(html, subject)

    log.info("=== Fin ===")


if __name__ == "__main__":
    main()
