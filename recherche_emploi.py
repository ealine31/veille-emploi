#!/usr/bin/env python3
"""
Veille quotidienne - Offres d'emploi analyse sensorielle en Occitanie.
Crée une GitHub Issue avec les offres trouvées.
GitHub envoie automatiquement un email de notification.

Configuration requise (variables d'environnement) :
  GITHUB_TOKEN      : fourni automatiquement par GitHub Actions
  GITHUB_REPOSITORY : fourni automatiquement par GitHub Actions (ex: ealine31/veille-emploi)
  FT_CLIENT_ID      : (optionnel) Client ID API France Travail
  FT_CLIENT_SECRET  : (optionnel) Client Secret API France Travail
"""

import os
import re
import time
import requests
import logging
import xml.etree.ElementTree as ET
from html import unescape
from datetime import datetime
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "ealine31/veille-emploi")

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
    "consommateur",
    "datavisualization",
    "visualisation de données",
    "data analyst",
]

# Termes envoyés aux moteurs de recherche : on dédoublonne les variantes
# (accentuées/non-accentuées) en gardant uniquement la forme accentuée.
_seen_normalized = set()
SEARCH_TERMS = []
for _kw in KEYWORDS:
    import unicodedata
    _norm = unicodedata.normalize("NFD", _kw).encode("ascii", "ignore").decode()
    if _norm not in _seen_normalized:
        _seen_normalized.add(_norm)
        SEARCH_TERMS.append(_kw)

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
    text = (offer.get("title", "") + " " + offer.get("description", "")).lower()
    return any(kw in text for kw in KEYWORDS)


def in_occitanie(offer: dict) -> bool:
    loc = offer.get("location", "").lower()
    if not loc:
        return True
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
    offers = []
    for term in SEARCH_TERMS:
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
    offers = []
    for term in SEARCH_TERMS:
        url = f"https://candidat.francetravail.fr/offres/recherche?motsCles={term}&lieux=76R&tri=0"
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
    for term in SEARCH_TERMS:
        url = f"https://www.hellowork.com/fr-fr/emploi/recherche.html?k={term}&l=Occitanie&s=created_desc"
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


def search_indeed() -> list:
    """Indeed via flux RSS — plus fiable que le scraping HTML."""
    offers = []
    for term in SEARCH_TERMS:
        r = safe_get("https://fr.indeed.com/rss", params={
            "q": term, "l": "Occitanie", "sort": "date", "fromage": "1"
        })
        if not r:
            continue
        try:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item"):
                title    = item.findtext("title", "").strip()
                link     = item.findtext("link", "").strip()
                desc_raw = item.findtext("description", "")
                desc     = unescape(re.sub(r"<[^>]+>", " ", desc_raw)).strip()[:400]
                # L'entreprise est souvent dans la balise <source> ou dans le titre "Poste - Entreprise"
                source_el = item.find("source")
                company  = source_el.text.strip() if source_el is not None else "Non précisé"
                if title:
                    offers.append({
                        "title":       title,
                        "company":     company,
                        "location":    "Occitanie",
                        "date":        datetime.today().strftime("%Y-%m-%d"),
                        "url":         link,
                        "description": desc,
                        "source":      "Indeed",
                    })
        except ET.ParseError as e:
            log.debug("Erreur RSS Indeed : %s", e)
        time.sleep(1.5)
    return offers


def search_linkedin() -> list:
    """LinkedIn — page publique de recherche d'offres."""
    offers = []
    for term in SEARCH_TERMS:
        r = safe_get(
            "https://www.linkedin.com/jobs/search/",
            params={"keywords": term, "location": "Occitanie, France", "sortBy": "DD", "f_TPR": "r86400"}
        )
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for card in soup.select("li[class*='result'], .job-search-card, [class*='job-card']"):
            title_el   = card.select_one("h3, h4, [class*='title']")
            company_el = card.select_one("[class*='company'], h4")
            loc_el     = card.select_one("[class*='location'], [class*='city']")
            link_el    = card.select_one("a[href]")
            if not title_el:
                continue
            href = link_el["href"] if link_el else ""
            if href and "?" in href:
                href = href.split("?")[0]
            offers.append({
                "title":       title_el.get_text(strip=True),
                "company":     company_el.get_text(strip=True) if company_el else "Non précisé",
                "location":    loc_el.get_text(strip=True) if loc_el else "Occitanie",
                "date":        datetime.today().strftime("%Y-%m-%d"),
                "url":         href,
                "description": card.get_text(" ", strip=True)[:400],
                "source":      "LinkedIn",
            })
        time.sleep(2)
    return offers


def search_google_jobs() -> list:
    """Google Jobs — résultats de recherche avec filtre emploi."""
    offers = []
    terms = [f"{kw} emploi Occitanie" for kw in SEARCH_TERMS]
    headers = {
        **HEADERS,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    for term in terms:
        try:
            r = SESSION.get(
                "https://www.google.com/search",
                params={"q": term, "hl": "fr", "gl": "fr", "ibp": "htl;jobs", "num": "20"},
                headers=headers,
                timeout=15,
            )
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "lxml")
            for card in soup.select("[class*='job'], [data-hveid], li[data-cid]"):
                title_el   = card.select_one("h3, h4, [class*='title'], [role='heading']")
                company_el = card.select_one("[class*='company'], [class*='employer']")
                loc_el     = card.select_one("[class*='location'], [class*='city']")
                link_el    = card.select_one("a[href]")
                if not title_el:
                    continue
                href = link_el["href"] if link_el else ""
                if href.startswith("/url?q="):
                    href = href.split("/url?q=")[1].split("&")[0]
                offers.append({
                    "title":       title_el.get_text(strip=True),
                    "company":     company_el.get_text(strip=True) if company_el else "Non précisé",
                    "location":    loc_el.get_text(strip=True) if loc_el else "Occitanie",
                    "date":        datetime.today().strftime("%Y-%m-%d"),
                    "url":         href,
                    "description": card.get_text(" ", strip=True)[:400],
                    "source":      "Google Jobs",
                })
        except Exception as e:
            log.debug("Erreur Google Jobs '%s' : %s", term, e)
        time.sleep(2)
    return offers


def search_meteojob() -> list:
    offers = []
    for term in SEARCH_TERMS:
        r = safe_get("https://www.meteojob.com/jobsearch/search", params={"what": term, "where": "Occitanie"})
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


# ── GitHub Issue ─────────────────────────────────────────────────────────────

def build_issue_body(offers: list, date_str: str) -> str:
    if not offers:
        return (
            f"Aucune offre trouvée aujourd'hui ({date_str}) en Occitanie "
            f"pour les termes surveillés.\n\n"
            f"**Mots-clés :** analyse sensorielle · évaluation sensorielle · "
            f"caractérisation sensorielle · organoleptique · dégustations · consumer\n\n"
            f"**Sources consultées :** France Travail · HelloWork · Meteojob"
        )

    lines = [f"**{len(offers)} offre(s) trouvée(s)** — {date_str}\n"]
    for o in offers:
        kw_found = [kw for kw in KEYWORDS if kw in (o.get("title","") + " " + o.get("description","")).lower()]
        badge = " · ".join(dict.fromkeys(kw_found))
        lines.append(
            f"---\n"
            f"### [{o.get('title','')}]({o.get('url','#')})\n"
            f"🏢 **{o.get('company','')}** &nbsp;|&nbsp; "
            f"📍 {o.get('location','')} &nbsp;|&nbsp; "
            f"📅 {o.get('date','')} &nbsp;|&nbsp; "
            f"*{o.get('source','')}*\n\n"
            + (f"🔑 `{badge}`\n\n" if badge else "")
            + f"{o.get('description','')[:300]}…\n"
        )

    lines.append(
        "\n---\n"
        "*Mots-clés surveillés : analyse sensorielle · évaluation sensorielle · "
        "caractérisation sensorielle · organoleptique · dégustations · consumer*\n"
        "*Sources : France Travail · HelloWork · Meteojob*"
    )
    return "\n".join(lines)


def create_github_issue(title: str, body: str) -> None:
    if not GITHUB_TOKEN:
        log.error("GITHUB_TOKEN non défini.")
        return

    r = requests.post(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/issues",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        json={"title": title, "body": body, "labels": ["veille-emploi"]},
        timeout=15,
    )
    if r.status_code == 201:
        log.info("✓ Issue créée : %s", r.json().get("html_url"))
    else:
        log.error("Erreur création issue : %s %s", r.status_code, r.text[:200])


# ── Point d'entrée ───────────────────────────────────────────────────────────

def main() -> None:
    today    = datetime.today()
    date_str = today.strftime("%d/%m/%Y")
    title    = f"🔬 Veille Emploi — Analyse Sensorielle Occitanie · {date_str}"

    log.info("=== Démarrage veille emploi %s ===", date_str)

    all_offers: list = []

    log.info("→ France Travail…")
    token = get_ft_token()
    if token:
        log.info("  Mode API (token OK)")
        all_offers.extend(search_france_travail_api(token))
    else:
        log.info("  Mode scraping (pas de token API)")
        all_offers.extend(search_france_travail_scraping())

    log.info("→ Indeed…")
    all_offers.extend(search_indeed())

    log.info("→ LinkedIn…")
    all_offers.extend(search_linkedin())

    log.info("→ Google Jobs…")
    all_offers.extend(search_google_jobs())

    log.info("→ HelloWork…")
    all_offers.extend(search_hellowork())

    log.info("→ Meteojob…")
    all_offers.extend(search_meteojob())

    filtered = deduplicate(
        [o for o in all_offers if contains_keyword(o) and in_occitanie(o)]
    )
    log.info("✓ %d offre(s) après filtrage", len(filtered))

    body = build_issue_body(filtered, date_str)
    create_github_issue(title, body)

    log.info("=== Fin ===")


if __name__ == "__main__":
    main()
