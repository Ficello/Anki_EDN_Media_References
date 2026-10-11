"""
Moteur de formatage des références / citations de médias selon les standards bibliographiques
(styles type Zotero : Vancouver/Biomédical, APA 7, Harvard, MLA, Compact, Personnalisé).
"""

import html
import re
from typing import List, Optional
from .metadata_extractor import MediaMetadata
from .i18n import tr


class ReferenceFormatter:
    """Formate les métadonnées de médias en citations selon le style choisi (Zotero-like)."""

    def __init__(self, template: Optional[str] = None, style: str = "vancouver"):
        """
        :param template: Modèle personnalisé avec placeholders {author}, {year}, {title}, {source}, {source_url}, {doi}, {license}
        :param style: "vancouver" | "apa" | "harvard" | "mla" | "compact" | "custom"
        """
        self.template = template
        self.style = style.lower() if style else "vancouver"

    def format_single(self, meta: MediaMetadata) -> str:
        """Formate la référence pour un seul média."""
        if self.style == "custom" and self.template:
            return self._format_custom_template(meta)
        elif self.style == "apa":
            return self._format_apa(meta)
        elif self.style == "harvard":
            return self._format_harvard(meta)
        elif self.style == "mla":
            return self._format_mla(meta)
        elif self.style == "compact":
            return self._format_compact(meta)
        else:
            return self._format_vancouver(meta)

    def _format_vancouver(self, meta: MediaMetadata) -> str:
        """
        Style Vancouver / Médical :
        [Auteurs]. [Titre]. [Revue / Source], [Année]. [Licence]. [Lien / DOI]
        """
        segments = []

        if meta.author:
            segments.append(html.escape(meta.author.rstrip(".")))

        if meta.title:
            cleaned_title = meta.title.rstrip(".")
            segments.append(f"<i>{html.escape(cleaned_title)}</i>")

        source_part = ""
        if meta.source:
            source_part = html.escape(meta.source)
            if meta.year:
                source_part += f", {html.escape(meta.year)}"
        elif meta.year:
            source_part = f"{html.escape(meta.year)}"

        if source_part:
            segments.append(source_part)

        if meta.license:
            if meta.license.startswith("http"):
                lic_name = self._beautify_license_url(meta.license)
                segments.append(f'sous licence <a href="{html.escape(meta.license)}" rel="noopener noreferrer">{html.escape(lic_name)}</a>')
            else:
                segments.append(f"({html.escape(meta.license)})")
        elif meta.copyright:
            segments.append(f"(&copy; {html.escape(meta.copyright)})")

        if meta.doi:
            doi_clean = html.escape(meta.doi)
            doi_url = f"https://doi.org/{meta.doi}" if not meta.doi.startswith("http") else meta.doi
            segments.append(f'DOI: <a href="{html.escape(doi_url)}" rel="noopener noreferrer">{doi_clean}</a>')
        elif meta.source_url:
            label = tr("source_link") if len(segments) > 0 else (meta.source or tr("source_link"))
            segments.append(f'<a href="{html.escape(meta.source_url)}" rel="noopener noreferrer">{html.escape(label)}</a>')

        if not segments:
            return ""

        citation = ". ".join(segments).strip()
        if not citation.endswith((".", "</a>", ")")):
            citation += "."

        return citation

    def _format_apa(self, meta: MediaMetadata) -> str:
        """
        Style APA 7e édition :
        Auteur(s) (Année). Titre. Source. DOI/URL
        """
        author = html.escape(meta.author.rstrip(".")) if meta.author else (html.escape(meta.source) if meta.source else "")
        year_str = f"({html.escape(meta.year)})" if meta.year else "(s.d.)"

        title_str = f"<i>{html.escape(meta.title.rstrip('.'))}</i>" if meta.title else ""
        source_str = html.escape(meta.source) if (meta.source and meta.author) else ""

        link_str = ""
        if meta.doi:
            doi_url = f"https://doi.org/{meta.doi}" if not meta.doi.startswith("http") else meta.doi
            link_str = f'<a href="{html.escape(doi_url)}" rel="noopener noreferrer">https://doi.org/{html.escape(meta.doi)}</a>'
        elif meta.source_url:
            link_str = f'<a href="{html.escape(meta.source_url)}" rel="noopener noreferrer">{html.escape(meta.source_url)}</a>'

        parts = [p for p in [author, year_str, title_str, source_str, link_str] if p]
        return ". ".join(parts).strip()

    def _format_harvard(self, meta: MediaMetadata) -> str:
        """
        Style Harvard (Auteur-Date) :
        Auteur, Année. Titre. Revue/Source. Disponible sur: URL
        """
        author = html.escape(meta.author.rstrip(".")) if meta.author else (html.escape(meta.source) if meta.source else "")
        year_str = html.escape(meta.year) if meta.year else ""
        header = f"{author}, {year_str}".strip(", ")

        title_str = html.escape(meta.title.rstrip(".")) if meta.title else ""
        source_str = f"<i>{html.escape(meta.source)}</i>" if (meta.source and meta.author) else ""

        link_str = ""
        if meta.doi:
            doi_url = f"https://doi.org/{meta.doi}" if not meta.doi.startswith("http") else meta.doi
            link_str = f'DOI: <a href="{html.escape(doi_url)}" rel="noopener noreferrer">{html.escape(meta.doi)}</a>'
        elif meta.source_url:
            link_str = f'<a href="{html.escape(meta.source_url)}" rel="noopener noreferrer">{html.escape(meta.source_url)}</a>'

        parts = [p for p in [header, title_str, source_str, link_str] if p]
        return ". ".join(parts).strip()

    def _format_mla(self, meta: MediaMetadata) -> str:
        """
        Style MLA 9e édition :
        Auteur. "Titre." Source, Année, URL/DOI.
        """
        author = f"{html.escape(meta.author.rstrip('.'))}." if meta.author else ""
        title_str = f'« {html.escape(meta.title.rstrip("."))} »' if meta.title else ""
        source_str = f"<i>{html.escape(meta.source)}</i>" if meta.source else ""
        year_str = html.escape(meta.year) if meta.year else ""

        middle_parts = [p for p in [source_str, year_str] if p]
        middle = ", ".join(middle_parts)

        link_str = ""
        if meta.doi:
            doi_url = f"https://doi.org/{meta.doi}" if not meta.doi.startswith("http") else meta.doi
            link_str = f'<a href="{html.escape(doi_url)}" rel="noopener noreferrer">doi:{html.escape(meta.doi)}</a>'
        elif meta.source_url:
            link_str = f'<a href="{html.escape(meta.source_url)}" rel="noopener noreferrer">{html.escape(meta.source_url)}</a>'

        parts = [p for p in [author, title_str, middle, link_str] if p]
        return " ".join(parts).strip()

    def _format_compact(self, meta: MediaMetadata) -> str:
        """Style Simple / Compact."""
        parts = []
        label = meta.source or meta.title or tr("source_link")
        if meta.source_url:
            parts.append(f'<a href="{html.escape(meta.source_url)}" rel="noopener noreferrer">{html.escape(label)}</a>')
        else:
            parts.append(html.escape(label))

        if meta.author:
            parts.append(f"par {html.escape(meta.author)}")

        if meta.year:
            parts.append(f"({html.escape(meta.year)})")

        if meta.license:
            parts.append(f"[{html.escape(meta.license)}]")

        return " ".join(parts).strip()

    def _format_custom_template(self, meta: MediaMetadata) -> str:
        """Remplacement selon template utilisateur."""
        t = self.template or ""
        mapping = {
            "{filename}": html.escape(meta.filename or ""),
            "{author}": html.escape(meta.author or ""),
            "{year}": html.escape(meta.year or ""),
            "{title}": html.escape(meta.title or ""),
            "{source}": html.escape(meta.source or ""),
            "{source_url}": html.escape(meta.source_url or ""),
            "{doi}": html.escape(meta.doi or ""),
            "{license}": html.escape(meta.license or ""),
            "{copyright}": html.escape(meta.copyright or ""),
            "{description}": html.escape(meta.description or ""),
        }
        for k, v in mapping.items():
            t = t.replace(k, v)
        return t.strip()

    def _beautify_license_url(self, url: str) -> str:
        if "creativecommons.org/licenses/" in url:
            m = re.search(r"licenses/([a-z0-9\-]+)/([0-9\.]+)", url, re.IGNORECASE)
            if m:
                code = m.group(1).upper()
                ver = m.group(2)
                return f"CC {code} {ver}"
        return "Creative Commons"

    def format_multiple(self, meta_list: List[MediaMetadata], existing_content: str = "") -> str:
        """Formate une liste de métadonnées de médias en évitant les doublons."""
        new_items = []
        for m in meta_list:
            if not m.has_useful_info():
                continue
            formatted = self.format_single(m)
            if not formatted:
                continue

            if self.is_duplicate(m, formatted, existing_content):
                continue
            if any(self.is_duplicate(m, formatted, item) for item in new_items):
                continue

            new_items.append(formatted)

        if not new_items:
            return ""

        return "<br>".join(new_items)

    def is_duplicate(self, meta: MediaMetadata, formatted_snippet: str, existing_html: str) -> bool:
        """Détecte si la référence existe déjà dans le champ existant."""
        if not existing_html:
            return False

        if meta.doi and meta.doi in existing_html:
            return True

        if meta.source_url and meta.source_url in existing_html:
            return True

        if meta.filename and meta.filename in existing_html:
            return True

        if formatted_snippet in existing_html:
            return True

        if meta.author and meta.source and meta.author in existing_html and meta.source in existing_html:
            return True

        return False
