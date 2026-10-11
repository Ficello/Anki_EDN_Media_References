"""
Moteur d'extraction de métadonnées pour les médias Anki (EXIF, IPTC, XMP, PNG, WebP, SVG, GIF)
et enrichissement académique web approfondi (JSON-LD, balises Highwire Press, Dublin Core, OpenGraph,
signatures médicales Dr/Pr, APIs Crossref & Wikimedia).
Filtrage anti-bruit strict pour éviter les pages de consentement Google / captcha / erreurs.
"""

import os
import re
import html
import json
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from PIL import Image, ExifTags, IptcImagePlugin
import xml.etree.ElementTree as ET


@dataclass
class MediaMetadata:
    filename: str
    author: Optional[str] = None
    year: Optional[str] = None
    title: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    doi: Optional[str] = None
    license: Optional[str] = None
    copyright: Optional[str] = None
    description: Optional[str] = None
    origin: str = "local"  # "local" | "html"
    raw_meta: Dict[str, Any] = field(default_factory=dict)

    def has_useful_info(self) -> bool:
        """Indique si au moins une information pertinente a été trouvée."""
        return bool(
            self.author or self.source or self.source_url or self.doi or
            self.license or self.copyright or (self.title and len(self.title) > 3) or self.year
        )


# Cache mémoire pour éviter les requêtes HTTP répétées
_ACADEMIC_CACHE: Dict[str, Dict[str, Any]] = {}


# Table de correspondance des domaines web médicaux et plateformes académiques
KNOWN_DOMAINS_MAP = {
    "commons.wikimedia.org": "Wikimedia Commons",
    "fr.wikipedia.org": "Wikipédia",
    "en.wikipedia.org": "Wikipedia",
    "wikipedia.org": "Wikipédia",
    "uness.fr": "UNESS / LiSA",
    "archives.uness.fr": "UNESS / LiSA",
    "livret.uness.fr": "UNESS / LiSA",
    "radiopaedia.org": "Radiopaedia",
    "imaios.com": "IMAIOS (e-Anatomy)",
    "has-sante.fr": "Haute Autorité de Santé (HAS)",
    "inserm.fr": "Inserm",
    "legifrance.gouv.fr": "Légifrance",
    "orpha.net": "Orphanet",
    "ncbi.nlm.nih.gov": "NCBI / PubMed",
    "pubmed.ncbi.nlm.nih.gov": "PubMed",
    "mdpi.com": "MDPI",
    "sciencedirect.com": "ScienceDirect",
    "thelancet.com": "The Lancet",
    "nejm.org": "NEJM",
    "nature.com": "Nature",
    "sfcardio.org": "SFC (Cardiologie)",
    "snfge.org": "SNFGE (Gastro-entérologie)",
    "sante.journaldesfemmes.fr": "Journal des Femmes Santé",
    "paediatrieschweiz.ch": "Pédiatrie Suisse",
    "diagnologic.com": "Diagnologic",
    "legacy.diagnologic.com": "Diagnologic",
    "aboutkidshealth.ca": "AboutKidsHealth",
    "dermatologyadvisor.com": "Dermatology Advisor",
    "allergolyon.fr": "AllergoLyon",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "instagram.com": "Instagram",
}


# Filtres de bruit : termes ou motifs techniques sans valeur d'auteur/source/titre
IGNORED_NOISE_PATTERNS = [
    # Consentement & Moteurs de recherche
    r"^avant d['’]accéder à google",
    r"^before you continue to google",
    r"^google\s*$",
    r"^google\.fr\s*$",
    r"^google\.com\s*$",
    r"^google search",
    r"^recherche google",
    r"^google images$",
    r"^google accounts",
    r"^sign in\b",
    r"^se connecter\b",
    r"^connexion\b",
    r"^attention required",
    r"^access denied",
    r"^403 forbidden",
    r"^404 not found",
    r"^just a moment",
    r"^uness login",
    r"^login\b",
    # Outils et métadonnées parasites
    r"^compressed by",
    r"^optimized by",
    r"^handmade software",
    r"^creator:\s*gd-jpeg",
    r"^adobe photoshop",
    r"^acd systems",
    r"^screen:\s*edp",
    r"^microsoft word",
    r"\.docx?$",
    r"\.indd$",
    r"^treble\s*-\s*whole note",
    r"^bass\s*\d+\s*-",
    r"^print$",
    r"^untitled",
    r"^cours$",
    r"^image$",
    r"^admin$",
    r"^administrator$",
    r"^contributeurs aux projets wikimedia$",
    r"^fondation wikimedia",
    r"^@\w+$",  # Handles twitter handles as author noise (ex: @dermadvisor)
    r"^nikon",
    r"^canon",
    r"^sony",
    r"^olympus",
    r"^fujifilm",
    r"^panasonic",
    r"^apple",
    r"^samsung",
    r"^\s*$",
]


def is_noise(text: Optional[str]) -> bool:
    if not text or not isinstance(text, str):
        return True
    cleaned = text.strip()
    if len(cleaned) < 2:
        return True
    for pat in IGNORED_NOISE_PATTERNS:
        if re.search(pat, cleaned, re.IGNORECASE):
            return True
    return False


def clean_string(val: Any) -> Optional[str]:
    """Nettoie une chaîne de caractères (décodage UTF-8, suppression d'octets nuls, balises XML résiduelles, unescape HTML)."""
    if val is None:
        return None
    if isinstance(val, bytes):
        try:
            if b"\x00" in val and len(val) % 2 == 0:
                try:
                    s = val.decode("utf-16le").rstrip("\x00")
                    if s.isprintable() and len(s.strip()) > 0:
                        val = s
                    else:
                        val = val.decode("utf-8", errors="ignore")
                except Exception:
                    val = val.decode("utf-8", errors="ignore")
            else:
                val = val.decode("utf-8", errors="ignore")
        except Exception:
            val = str(val)
    elif not isinstance(val, str):
        val = str(val)

    val = re.sub(r"<[^>]+>", " ", val)
    val = val.replace("\x00", "").strip()
    val = re.sub(r"\s+", " ", val)
    val = html.unescape(val)
    if is_noise(val):
        return None
    return val


def beautify_domain(domain_or_url: str) -> str:
    """Retourne un nom de source élégant à partir d'un nom de domaine ou URL."""
    if not domain_or_url:
        return "Source Web"

    if "://" in domain_or_url:
        parsed = urllib.parse.urlparse(domain_or_url)
        netloc = parsed.netloc.lower()
    else:
        netloc = domain_or_url.lower()

    netloc = netloc.replace("www.", "")

    for domain_key, friendly_name in KNOWN_DOMAINS_MAP.items():
        if netloc == domain_key or netloc.endswith("." + domain_key):
            return friendly_name

    return netloc


def extract_urls(text: str) -> List[str]:
    """Extrait les URLs valides d'un texte en préservant parenthèses et paramètres complets."""
    if not text:
        return []
    urls = re.findall(r"https?://[^\s<>'\"`]+", text)
    cleaned_urls = []
    for u in urls:
        u = re.sub(r"[\.,;]+$", "", u)
        if u:
            cleaned_urls.append(u)
    return cleaned_urls


def parse_google_image_url(url: str) -> Dict[str, Optional[str]]:
    """
    Décode les paramètres imgrefurl / imgurl / q / url d'une URL de redirection Google Images.
    Extrait l'URL réelle de la page source hébergeant l'image.
    """
    unescaped = html.unescape(url)
    parsed = urllib.parse.urlparse(unescaped)
    qs = urllib.parse.parse_qs(parsed.query)

    imgrefurl = qs.get("imgrefurl", [""])[0]
    imgurl = qs.get("imgurl", [""])[0]
    url_param = qs.get("url", [""])[0]
    query = qs.get("q", [""])[0]

    clean_query = None
    if query:
        unquoted = urllib.parse.unquote_plus(query).strip()
        if unquoted and not is_noise(unquoted):
            clean_query = unquoted

    target_webpage = imgrefurl or url_param or None
    if not target_webpage and imgurl and not ("google." in imgurl or "googleusercontent.com" in imgurl):
        target_webpage = imgurl

    return {
        "real_source_url": target_webpage,
        "query": clean_query,
        "direct_img_url": imgurl or None,
        "original_url": unescaped
    }


def extract_offline_academic_hints(url: str, filename: str) -> Dict[str, str]:
    """Extrait les indices académiques (année, auteurs potentiels) depuis l'URL et le nom de fichier."""
    hints = {}
    combined = f"{url} {filename}"

    # 1. Année (1980 - 2029)
    year_m = re.search(r"/(19[89]\d|20[0-2]\d)/", url) or re.search(r"[\b_-](19[89]\d|20[0-2]\d)[\b_.-]", combined)
    if year_m:
        hints["year"] = year_m.group(1)

    # 2. Détection de DOI dans l'URL (ex: 10.3390/ijms24076709)
    doi_m = re.search(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", url)
    if doi_m:
        d = doi_m.group(1)
        d = re.sub(r"\.(?:png|jpg|jpeg|gif|webp|svg|pdf)$", "", d, flags=re.I)
        hints["doi"] = d

    # 3. Détection d'auteurs explicites dans le nom de fichier (ex: Martin-Theiler, Apartis-Bourdieu-et-Charles-Pierre-Jedynak)
    author_m = re.search(r"(?:^|[\/_-])([A-Z][a-z]+(?:-[A-Z][a-z]+)?(?:-et-[A-Z][a-z]+(?:-[A-Z][a-z]+)?)+)", filename)
    if author_m:
        cleaned_author = author_m.group(1).replace("-et-", " et ").replace("-", " ")
        if not is_noise(cleaned_author):
            hints["author"] = cleaned_author

    return hints


def fetch_wikimedia_metadata(wiki_url: str, timeout: float = 4.0) -> Optional[Dict[str, Any]]:
    """Interroge l'API Wikimedia Commons pour extraire auteur exact, année, licence et titre."""
    if wiki_url in _ACADEMIC_CACHE:
        return _ACADEMIC_CACHE[wiki_url]

    m = re.search(r"(?:File|Fichier):(.+)$", wiki_url, re.IGNORECASE)
    if not m:
        return None

    file_title = "File:" + urllib.parse.unquote(m.group(1).split("&")[0].split("#")[0])
    api_url = f"https://commons.wikimedia.org/w/api.php?action=query&titles={urllib.parse.quote(file_title)}&prop=imageinfo&iiprop=extmetadata&format=json"

    req = urllib.request.Request(api_url, headers={"User-Agent": "AnkiEDNMediaReferences/1.0 (academic; anki addon)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            pages = data.get("query", {}).get("pages", {})
            for pid, pdata in pages.items():
                imginfo = pdata.get("imageinfo", [])
                if imginfo:
                    ext = imginfo[0].get("extmetadata", {})

                    # Auteur
                    artist_raw = ext.get("Artist", {}).get("value", "")
                    artist = clean_string(re.sub(r"<[^>]+>", " ", artist_raw))

                    # Année
                    dt = ext.get("DateTimeOriginal", {}).get("value") or ext.get("DateTime", {}).get("value") or ""
                    year_m = re.search(r"\b(19\d\d|20\d\d)\b", dt)
                    year = year_m.group(1) if year_m else None

                    # Licence
                    license_name = ext.get("LicenseShortName", {}).get("value", "")

                    # Titre / Description
                    desc_raw = ext.get("ObjectName", {}).get("value", "") or ext.get("ImageDescription", {}).get("value", "")
                    desc = clean_string(re.sub(r"<[^>]+>", " ", desc_raw))
                    if desc and len(desc) > 80:
                        desc = desc[:80] + "..."

                    res = {
                        "author": artist,
                        "year": year,
                        "license": license_name or None,
                        "title": desc,
                        "source": "Wikimedia Commons",
                        "source_url": wiki_url
                    }
                    _ACADEMIC_CACHE[wiki_url] = res
                    return res
    except Exception:
        pass

    return None


def fetch_crossref_metadata(doi: str, timeout: float = 4.0) -> Optional[Dict[str, Any]]:
    """Interroge l'API Crossref pour extraire la citation complète d'un DOI scientifique."""
    if doi in _ACADEMIC_CACHE:
        return _ACADEMIC_CACHE[doi]

    url = f"https://api.crossref.org/works/{doi}"
    req = urllib.request.Request(url, headers={"User-Agent": "AnkiEDNMediaReferences/1.0 (academic; anki addon)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8")).get("message", {})
            authors = []
            for a in data.get("author", []):
                given = a.get("given", "")
                family = a.get("family", "")
                if family:
                    initial = f" {given[0]}." if given else ""
                    authors.append(f"{family}{initial}")

            if len(authors) > 3:
                author_str = ", ".join(authors[:3]) + " et al."
            elif authors:
                author_str = ", ".join(authors)
            else:
                author_str = None

            title = data.get("title", [None])[0]
            journal = data.get("container-title", [None])[0]
            year = str(data.get("issued", {}).get("date-parts", [[None]])[0][0] or "") or None

            res = {
                "author": author_str,
                "title": title,
                "source": journal,
                "year": year,
                "doi": doi,
                "source_url": data.get("URL", f"https://doi.org/{doi}")
            }
            _ACADEMIC_CACHE[doi] = res
            return res
    except Exception:
        pass

    return None


def fetch_web_page_academic_meta(url: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """
    Scrape en profondeur les métadonnées académiques d'une page web :
    - Bloque strictement les domaines Google (qui renvoient des pages de consentement GDPR)
    - JSON-LD structured data (schema.org Article/MedicalWebPage)
    - Balises Highwire Press & Dublin Core & OpenGraph
    - Détection de signatures médicales textuelles (Dr / Pr / Par...)
    """
    if not url or url in _ACADEMIC_CACHE:
        return _ACADEMIC_CACHE.get(url)

    # 1. Protection stricte : ne jamais scraper Google directement
    domain = urllib.parse.urlparse(url).netloc.lower()
    if "google." in domain or "googleusercontent.com" in domain:
        return None

    # 2. Vérification spéciale Wikimedia
    if "commons.wikimedia.org" in url or "wikipedia.org/wiki/File:" in url or "wikipedia.org/wiki/Fichier:" in url:
        w_meta = fetch_wikimedia_metadata(url, timeout=timeout)
        if w_meta:
            return w_meta

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7"
    })

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_bytes = resp.read(500000)  # 500 Ko
            charset = resp.headers.get_content_charset() or "utf-8"
            text = content_bytes.decode(charset, errors="ignore")

            res = {"source_url": url}

            # 1. JSON-LD parsing (Schémas structurés Schema.org)
            json_ld_matches = re.findall(r'<script\s+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.I | re.DOTALL)
            for jld in json_ld_matches:
                try:
                    data = json.loads(jld.strip())
                    items = data if isinstance(data, list) else [data]
                    if isinstance(data, dict) and "@graph" in data:
                        items = data["@graph"]

                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        # Auteur
                        author_obj = item.get("author")
                        if author_obj and "author" not in res:
                            if isinstance(author_obj, list):
                                a_names = [a.get("name", "") if isinstance(a, dict) else str(a) for a in author_obj]
                                clean_names = [clean_string(n) for n in a_names if clean_string(n)]
                                if clean_names:
                                    res["author"] = ", ".join(clean_names[:3]) + (" et al." if len(clean_names) > 3 else "")
                            elif isinstance(author_obj, dict):
                                a_name = clean_string(author_obj.get("name"))
                                if a_name:
                                    res["author"] = a_name
                            elif isinstance(author_obj, str):
                                a_name = clean_string(author_obj)
                                if a_name:
                                    res["author"] = a_name

                        # Date / Année
                        date_str = item.get("datePublished") or item.get("dateModified") or item.get("dateCreated")
                        if date_str and "year" not in res:
                            ym = re.search(r"\b(19[89]\d|20[0-2]\d)\b", str(date_str))
                            if ym:
                                res["year"] = ym.group(1)

                        # Titre
                        headline = item.get("headline") or item.get("name")
                        if headline and "title" not in res and len(headline) > 5:
                            cleaned_head = clean_string(headline)
                            if cleaned_head and not is_noise(cleaned_head):
                                res["title"] = cleaned_head

                        # Éditeur / Revue
                        pub = item.get("publisher")
                        if pub and "source" not in res:
                            if isinstance(pub, dict):
                                p_name = clean_string(pub.get("name"))
                                if p_name and not is_noise(p_name):
                                    res["source"] = p_name
                            elif isinstance(pub, str):
                                p_name = clean_string(pub)
                                if p_name and not is_noise(p_name):
                                    res["source"] = p_name
                except Exception:
                    pass

            # 2. Meta Tags (Highwire Press, Dublin Core, OpenGraph, Twitter Cards)
            # Auteurs
            if "author" not in res or not res["author"]:
                author_matches = re.findall(r'<meta\s+(?:name|property)=["\'](?:citation_author|dc\.creator|author|article:author|twitter:creator)["\']\s+content=["\']([^"\']+)["\']', text, re.I)
                if not author_matches:
                    author_matches = re.findall(r'<meta\s+content=["\']([^"\']+)["\']\s+(?:name|property)=["\'](?:citation_author|dc\.creator|author|article:author|twitter:creator)["\']', text, re.I)
                if author_matches:
                    clean_a = [clean_string(a) for a in author_matches if clean_string(a) and not a.startswith("http")]
                    if clean_a:
                        res["author"] = ", ".join(clean_a[:3]) + (" et al." if len(clean_a) > 3 else "")

            # Titre
            if "title" not in res or not res["title"]:
                title_matches = re.findall(r'<meta\s+(?:name|property)=["\'](?:citation_title|og:title|dc\.title|twitter:title)["\']\s+content=["\']([^"\']+)["\']', text, re.I)
                if not title_matches:
                    title_matches = re.findall(r'<meta\s+content=["\']([^"\']+)["\']\s+(?:name|property)=["\'](?:citation_title|og:title|dc\.title|twitter:title)["\']', text, re.I)
                if title_matches:
                    t = clean_string(title_matches[0])
                    if t and not is_noise(t):
                        res["title"] = t
                else:
                    h1 = re.search(r'<h1[^>]*>(.*?)</h1>', text, re.I | re.DOTALL)
                    if h1:
                        t = clean_string(h1.group(1))
                        if t and not is_noise(t):
                            res["title"] = t
                    else:
                        title_tag = re.search(r'<title[^>]*>(.*?)</title>', text, re.I | re.DOTALL)
                        if title_tag:
                            t = clean_string(title_tag.group(1))
                            if t and not is_noise(t):
                                res["title"] = t

            # Année / Date
            if "year" not in res or not res["year"]:
                date_matches = re.findall(r'<meta\s+(?:name|property)=["\'](?:citation_publication_date|citation_date|citation_year|article:published_time|article:modified_time|dc\.date|date|publishdate)["\']\s+content=["\']([^"\']+)["\']', text, re.I)
                if date_matches:
                    ym = re.search(r"\b(19[89]\d|20[0-2]\d)\b", date_matches[0])
                    if ym:
                        res["year"] = ym.group(1)
                else:
                    time_m = re.search(r'<time[^>]*datetime=["\']([^"\']+)["\']', text, re.I)
                    if time_m:
                        ym = re.search(r"\b(19[89]\d|20[0-2]\d)\b", time_m.group(1))
                        if ym:
                            res["year"] = ym.group(1)
                    else:
                        text_date = re.search(r'(?:publi[ée]|mis à jour|date|le)\s*[:\s]\s*(\d{1,2}[\/\.-]\d{1,2}[\/\.-](19\d\d|20\d\d)|\d{1,2}\s+[a-zéû]+\s+(19\d\d|20\d\d)|(19\d\d|20\d\d))', text, re.I)
                        if text_date:
                            ym = re.search(r"\b(19\d\d|20\d\d)\b", text_date.group(0))
                            if ym:
                                res["year"] = ym.group(1)

            # DOI
            if "doi" not in res:
                dois = re.findall(r'<meta\s+name=["\'](?:citation_doi|dc\.identifier)["\']\s+content=["\']([^"\']+)["\']', text, re.I)
                if dois:
                    res["doi"] = dois[0].strip()

            # Revue / Source
            if "source" not in res or not res["source"]:
                site_matches = re.findall(r'<meta\s+(?:name|property)=["\'](?:citation_journal_title|og:site_name|citation_publisher|publisher)["\']\s+content=["\']([^"\']+)["\']', text, re.I)
                if site_matches:
                    s_name = clean_string(site_matches[0])
                    if s_name and not is_noise(s_name):
                        res["source"] = s_name
                if "source" not in res or not res["source"]:
                    res["source"] = beautify_domain(url)

            # 3. Extraction médicale textuelle (Dr / Pr / Par...)
            if "author" not in res or not res["author"] or is_noise(res["author"]):
                doc_m = re.search(r'(?:Par|Écrit par|Rédigé par|Auteur\s*:|Par le)\s*(?:le\s+)?(Dr\.?\s+[A-ZÀ-ÖØ-ß][a-zà-öø-ÿ\-]+(?:\s+[A-ZÀ-ÖØ-ß][a-zà-öø-ÿ\-]+){1,2}|Pr\.?\s+[A-ZÀ-ÖØ-ß][a-zà-öø-ÿ\-]+(?:\s+[A-ZÀ-ÖØ-ß][a-zà-öø-ÿ\-]+){1,2})', text, re.I)
                if doc_m:
                    res["author"] = clean_string(doc_m.group(1))
                else:
                    author_html = re.search(r'<(?:span|a|div|p)[^>]*(?:class|rel|itemprop)=["\'][^"\']*(?:author|byline|auteur|writer)[^"\']*["\'][^>]*>(.*?)</(?:span|a|div|p)>', text, re.I | re.DOTALL)
                    if author_html:
                        a_clean = clean_string(re.sub(r'<[^>]+>', '', author_html.group(1)))
                        if a_clean and len(a_clean) < 60 and not a_clean.lower().startswith("http") and not is_noise(a_clean):
                            res["author"] = a_clean

            # Nettoyage des suffixes de sites dans les titres
            if "title" in res and res["title"]:
                t = res["title"]
                t = re.sub(r"\s*[-|–—•]\s*(?:Journal des Femmes|Dermatology Advisor|Wikipédia|YouTube|PubMed|MDPI|Pédiatrie Suisse|AboutKidsHealth|Diagnologic|AllergoLyon).*$", "", t, flags=re.I)
                res["title"] = t.strip()

            _ACADEMIC_CACHE[url] = res
            return res

    except Exception:
        pass

    return None


def extract_html_context_metadata(html_content: str, enable_online: bool = True) -> List[MediaMetadata]:
    """
    Extrait les métadonnées académiques et contextuelles contenues directement dans le HTML
    avec enrichissement en ligne optionnel (APIs et scraping).
    """
    results: List[MediaMetadata] = []
    if not html_content:
        return results

    # 1. Recherche des balises <a href="..."><img ...></a>
    pattern_anchor_img = re.compile(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(?:(?!</a>).)*?<img\s+([^>]+)>',
        re.IGNORECASE | re.DOTALL
    )

    for match in pattern_anchor_img.finditer(html_content):
        href = match.group(1).strip()
        img_tag = match.group(2)

        src_m = re.search(r'src=["\']([^"\']+)["\']', img_tag, re.IGNORECASE)
        if not src_m:
            continue
        src = urllib.parse.unquote(src_m.group(1).strip())
        filename = os.path.basename(src)

        alt_m = re.search(r'(?:alt|title)=["\']([^"\']+)["\']', img_tag, re.IGNORECASE)
        alt_text = clean_string(alt_m.group(1)) if alt_m else None

        meta = MediaMetadata(filename=filename)
        target_scrape_url = None

        if "google." in href:
            g_info = parse_google_image_url(href)
            if g_info["real_source_url"]:
                real_url = g_info["real_source_url"]
                meta.source_url = real_url
                meta.source = beautify_domain(real_url)
                target_scrape_url = real_url
                if alt_text and not is_noise(alt_text):
                    meta.title = alt_text
                elif g_info["query"]:
                    meta.title = g_info["query"]
            else:
                # Lien de recherche Google sans page cible
                meta.source_url = href
                meta.source = "Recherche Google Images"
                if alt_text and not is_noise(alt_text):
                    meta.title = alt_text
                elif g_info["query"]:
                    meta.title = g_info["query"]
                target_scrape_url = None  # Ne pas scraper Google !
        elif href.startswith("http://") or href.startswith("https://"):
            target_url = html.unescape(href)
            meta.source_url = target_url
            meta.source = beautify_domain(target_url)
            target_scrape_url = target_url
            if alt_text and not is_noise(alt_text):
                meta.title = alt_text

        # Extraction hors-ligne
        if meta.source_url:
            hints = extract_offline_academic_hints(meta.source_url, filename)
            for k, v in hints.items():
                if v and getattr(meta, k, None) is None:
                    setattr(meta, k, v)

        # Enrichissement en ligne si URL de page réelle externe
        if enable_online and target_scrape_url:
            academic_data = None
            if meta.doi:
                academic_data = fetch_crossref_metadata(meta.doi)
            if not academic_data and ("wikimedia.org" in target_scrape_url or "wikipedia.org" in target_scrape_url):
                academic_data = fetch_wikimedia_metadata(target_scrape_url)
            if not academic_data:
                academic_data = fetch_web_page_academic_meta(target_scrape_url)

            if academic_data:
                for field_name in ("author", "year", "title", "source", "doi", "license"):
                    val = academic_data.get(field_name)
                    if val and not is_noise(str(val)):
                        setattr(meta, field_name, val)

        if meta.has_useful_info():
            results.append(meta)

    # 2. Recherche des <img src="https://..."> directes
    pattern_direct_img = re.compile(r'<img\s+([^>]+)>', re.IGNORECASE)
    for match in pattern_direct_img.finditer(html_content):
        img_attrs = match.group(1)
        src_m = re.search(r'src=["\'](https?://[^"\']+)["\']', img_attrs, re.IGNORECASE)
        if src_m:
            img_url = src_m.group(1).strip()
            filename = os.path.basename(urllib.parse.urlparse(img_url).path) or "image_web"

            if any(r.filename == filename or r.source_url == img_url for r in results):
                continue

            alt_m = re.search(r'(?:alt|title)=["\']([^"\']+)["\']', img_attrs, re.IGNORECASE)
            alt_text = clean_string(alt_m.group(1)) if alt_m else None

            meta = MediaMetadata(
                filename=filename,
                source_url=img_url,
                source=beautify_domain(img_url),
                title=alt_text if (alt_text and not is_noise(alt_text)) else None
            )

            hints = extract_offline_academic_hints(img_url, filename)
            for k, v in hints.items():
                if v and getattr(meta, k, None) is None:
                    setattr(meta, k, v)

            if enable_online and not ("google." in img_url or "googleusercontent.com" in img_url):
                academic_data = None
                if meta.doi:
                    academic_data = fetch_crossref_metadata(meta.doi)
                if not academic_data and ("wikimedia.org" in img_url or "wikipedia.org" in img_url):
                    academic_data = fetch_wikimedia_metadata(img_url)
                if not academic_data:
                    academic_data = fetch_web_page_academic_meta(img_url)

                if academic_data:
                    for field_name in ("author", "year", "title", "source", "doi", "license"):
                        val = academic_data.get(field_name)
                        if val and not is_noise(str(val)):
                            setattr(meta, field_name, val)

            if meta.has_useful_info():
                results.append(meta)

    return results


def extract_xmp_metadata(xmp_data: Any) -> Dict[str, str]:
    """Extrait les champs XMP (Dublin Core, Photoshop, Creative Commons, Rights)."""
    if isinstance(xmp_data, bytes):
        text = xmp_data.decode("utf-8", errors="ignore")
    elif isinstance(xmp_data, str):
        text = xmp_data
    else:
        return {}

    meta = {}
    patterns = {
        "author": [
            r"<dc:creator[^>]*>.*?<rdf:li[^>]*>(.*?)</rdf:li>",
            r"<dc:creator[^>]*>(.*?)</dc:creator>",
            r"<photoshop:AuthorsPosition>(.*?)</photoshop:AuthorsPosition>",
            r"<cc:attributionName>(.*?)</cc:attributionName>",
            r"<plus:LicensorName>(.*?)</plus:LicensorName>",
            r"<plus:CopyrightOwnerName>(.*?)</plus:CopyrightOwnerName>",
        ],
        "copyright": [
            r"<dc:rights[^>]*>.*?<rdf:li[^>]*>(.*?)</rdf:li>",
            r"<dc:rights[^>]*>(.*?)</dc:rights>",
            r"<xmpRights:Owner[^>]*>.*?<rdf:li[^>]*>(.*?)</rdf:li>",
        ],
        "title": [
            r"<dc:title[^>]*>.*?<rdf:li[^>]*>(.*?)</rdf:li>",
            r"<dc:title[^>]*>(.*?)</dc:title>",
            r"<photoshop:Headline>(.*?)</photoshop:Headline>",
        ],
        "description": [
            r"<dc:description[^>]*>.*?<rdf:li[^>]*>(.*?)</rdf:li>",
            r"<dc:description[^>]*>(.*?)</dc:description>",
            r"<photoshop:CaptionWriter>(.*?)</photoshop:CaptionWriter>",
        ],
        "source": [
            r"<photoshop:Source>(.*?)</photoshop:Source>",
            r"<photoshop:Credit>(.*?)</photoshop:Credit>",
            r"<dc:source[^>]*>(.*?)</dc:source>",
            r"<dc:publisher[^>]*>.*?<rdf:li[^>]*>(.*?)</rdf:li>",
        ],
        "source_url": [
            r"<cc:attributionURL[^>]*rdf:resource=[\"'](.*?)[\"']",
            r"<xmpRights:WebStatement>(.*?)</xmpRights:WebStatement>",
            r"<plus:LicensorURL>(.*?)</plus:LicensorURL>",
        ],
        "license": [
            r"<cc:license[^>]*rdf:resource=[\"'](.*?)[\"']",
            r"<xmpRights:UsageTerms[^>]*>.*?<rdf:li[^>]*>(.*?)</rdf:li>",
            r"<xmpRights:UsageTerms[^>]*>(.*?)</xmpRights:UsageTerms>",
        ],
    }

    for key, regex_list in patterns.items():
        for pat in regex_list:
            match = re.search(pat, text, re.DOTALL | re.IGNORECASE)
            if match:
                val = clean_string(match.group(1))
                if val:
                    meta[key] = val
                    break

    return meta


def extract_svg_metadata(svg_path: str) -> Dict[str, str]:
    """Extrait les métadonnées d'un fichier vectoriel SVG."""
    meta = {}
    try:
        with open(svg_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        title_m = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", content, re.DOTALL | re.IGNORECASE)
        if title_m:
            meta["title"] = clean_string(title_m.group(1))

        creator_m = re.search(r"<dc:creator[^>]*>.*?<rdf:li[^>]*>(.*?)</rdf:li>", content, re.DOTALL | re.IGNORECASE)
        if creator_m:
            meta["author"] = clean_string(creator_m.group(1))

        rights_m = re.search(r"<dc:rights[^>]*>.*?<rdf:li[^>]*>(.*?)</rdf:li>", content, re.DOTALL | re.IGNORECASE)
        if rights_m:
            meta["copyright"] = clean_string(rights_m.group(1))

        license_m = re.search(r"<cc:license[^>]*rdf:resource=[\"'](.*?)[\"']", content, re.IGNORECASE)
        if license_m:
            meta["license"] = clean_string(license_m.group(1))

        urls = extract_urls(content)
        wikimedia_urls = [u for u in urls if "commons.wikimedia.org" in u or "wikipedia.org" in u or "uness.fr" in u]
        if wikimedia_urls:
            meta["source_url"] = wikimedia_urls[0]

    except Exception:
        pass
    return meta


def extract_media_metadata(media_path: str) -> MediaMetadata:
    """
    Extrait toutes les métadonnées disponibles d'un fichier média local.
    Prend en charge JPEG, PNG, WebP, GIF, TIFF, SVG, etc.
    """
    filename = os.path.basename(media_path)
    result = MediaMetadata(filename=filename)

    if not os.path.isfile(media_path):
        return result

    ext = os.path.splitext(filename)[1].lower()

    if ext == ".svg":
        svg_meta = extract_svg_metadata(media_path)
        for k, v in svg_meta.items():
            if v and getattr(result, k, None) is None:
                setattr(result, k, v)
        result.raw_meta = svg_meta
        return result

    raw_meta: Dict[str, Any] = {}

    try:
        with Image.open(media_path) as img:
            info = img.info

            # 1. EXIF standard & Windows XP tags
            exif = img.getexif()
            if exif:
                exif_dict = {}
                for tag_id, val in exif.items():
                    tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                    cleaned = clean_string(val)
                    if cleaned:
                        exif_dict[tag_name] = cleaned

                raw_meta["exif"] = exif_dict

                if not result.author:
                    result.author = exif_dict.get("Artist") or exif_dict.get("XPAuthor")
                if not result.copyright:
                    result.copyright = exif_dict.get("Copyright")
                if not result.title:
                    result.title = exif_dict.get("XPTitle") or exif_dict.get("ImageDescription")
                if not result.description:
                    result.description = exif_dict.get("XPComment") or exif_dict.get("UserComment")

                # Date / Année EXIF
                dt = exif_dict.get("DateTime") or exif_dict.get("DateTimeOriginal") or exif_dict.get("DateTimeDigitized")
                if dt:
                    ym = re.search(r"\b(19\d\d|20\d\d)\b", str(dt))
                    if ym:
                        result.year = ym.group(1)

            # 2. IPTC (Photoshop / IIM)
            try:
                iptc = IptcImagePlugin.getiptcinfo(img)
                if iptc:
                    iptc_dict = {}
                    tag_mapping = {
                        (2, 80): "author",
                        (2, 85): "author_title",
                        (2, 110): "credit",
                        (2, 115): "source",
                        (2, 116): "copyright",
                        (2, 5): "title",
                        (2, 120): "caption",
                        (2, 230): "source_url",
                        (2, 55): "date",
                    }
                    for k, val in iptc.items():
                        field_name = tag_mapping.get(k, f"tag_{k}")
                        cleaned = clean_string(val)
                        if cleaned:
                            iptc_dict[field_name] = cleaned

                    raw_meta["iptc"] = iptc_dict

                    if not result.author and "author" in iptc_dict:
                        result.author = iptc_dict["author"]
                    if not result.source and "source" in iptc_dict:
                        result.source = iptc_dict["source"]
                    elif not result.source and "credit" in iptc_dict:
                        result.source = iptc_dict["credit"]
                    if not result.copyright and "copyright" in iptc_dict:
                        result.copyright = iptc_dict["copyright"]
                    if not result.title and "title" in iptc_dict:
                        result.title = iptc_dict["title"]
                    if not result.description and "caption" in iptc_dict:
                        result.description = iptc_dict["caption"]
                    if not result.source_url and "source_url" in iptc_dict:
                        result.source_url = iptc_dict["source_url"]
                    if not result.year and "date" in iptc_dict:
                        ym = re.search(r"\b(19\d\d|20\d\d)\b", str(iptc_dict["date"]))
                        if ym:
                            result.year = ym.group(1)
            except Exception:
                pass

            # 3. XMP (Extensible Metadata Platform)
            for xmp_key in ["xmp", "XML:com.adobe.xmp"]:
                if xmp_key in info:
                    xmp_meta = extract_xmp_metadata(info[xmp_key])
                    if xmp_meta:
                        raw_meta["xmp"] = xmp_meta
                        if not result.author and "author" in xmp_meta:
                            result.author = xmp_meta["author"]
                        if not result.copyright and "copyright" in xmp_meta:
                            result.copyright = xmp_meta["copyright"]
                        if not result.title and "title" in xmp_meta:
                            result.title = xmp_meta["title"]
                        if not result.description and "description" in xmp_meta:
                            result.description = xmp_meta["description"]
                        if not result.source and "source" in xmp_meta:
                            result.source = xmp_meta["source"]
                        if not result.source_url and "source_url" in xmp_meta:
                            result.source_url = xmp_meta["source_url"]
                        if not result.license and "license" in xmp_meta:
                            result.license = xmp_meta["license"]

            # 4. PNG Chunks / Info tags
            png_dict = {}
            for k, val in info.items():
                k_lower = str(k).lower()
                if k_lower in ["author", "artist", "copyright", "description", "source", "comment", "title", "url"]:
                    cleaned = clean_string(val)
                    if cleaned:
                        png_dict[k_lower] = cleaned

            if png_dict:
                raw_meta["png"] = png_dict

                if not result.author and "author" in png_dict:
                    result.author = png_dict["author"]
                elif not result.author and "artist" in png_dict:
                    result.author = png_dict["artist"]

                if not result.copyright and "copyright" in png_dict:
                    result.copyright = png_dict["copyright"]

                if not result.title and "title" in png_dict:
                    result.title = png_dict["title"]

                if not result.source and "source" in png_dict:
                    result.source = png_dict["source"]

                if "comment" in png_dict:
                    comment = png_dict["comment"]
                    urls = extract_urls(comment)
                    if urls and not result.source_url:
                        result.source_url = urls[0]
                    if "wikimedia" in comment.lower() and not result.source:
                        result.source = "Wikimedia Commons"
                    elif "uness" in comment.lower() and not result.source:
                        result.source = "UNESS / LiSA"

                if "url" in png_dict and not result.source_url:
                    result.source_url = png_dict["url"]

    except Exception as e:
        raw_meta["error"] = str(e)

    if result.source_url and not result.source:
        result.source = beautify_domain(result.source_url)

    result.raw_meta = raw_meta
    return result

