"""
Moteur de traitement des notes Anki pour l'extraction et l'attribution des références de médias
(fichiers locaux et images issues du web avec enrichissement académique).
"""

import os
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, Callable

from .metadata_extractor import (
    extract_media_metadata,
    extract_html_context_metadata,
    MediaMetadata
)
from .field_mapper import FieldMapper
from .formatter import ReferenceFormatter
from .config import get_config


@dataclass
class NoteSnapshot:
    """Instantané immuable en mémoire d'une note, totalement découplé de la base SQLite."""
    nid: int
    target_field: str
    candidate_fields: List[str]
    fields: Dict[str, str]
    tags: List[str] = field(default_factory=list)


@dataclass
class NoteProcessResult:
    nid: int
    modified: bool
    target_field: Optional[str]
    old_value: str
    new_value: str
    media_extracted: List[MediaMetadata] = field(default_factory=list)
    all_media_count: int = 0
    local_meta_count: int = 0
    has_missing_source: bool = False
    tag_to_add: Optional[str] = None
    flag_to_set: int = 0
    error: Optional[str] = None


@dataclass
class BatchProcessStats:
    total_notes: int = 0
    notes_processed: int = 0
    notes_modified: int = 0
    media_found: int = 0
    media_with_meta: int = 0
    local_meta_found: int = 0
    notes_missing_source: int = 0
    errors: List[str] = field(default_factory=list)
    results: List[NoteProcessResult] = field(default_factory=list)


# Stockage global persistant de la dernière session d'extraction
LAST_EXTRACTION_STATS: Optional[BatchProcessStats] = None


def get_last_stats() -> Optional[BatchProcessStats]:
    """Renvoie les statistiques du dernier traitement par lots effectué."""
    return LAST_EXTRACTION_STATS


def extract_media_filenames(html_content: str, include_images: bool = True, include_audio: bool = True, include_video: bool = True) -> List[str]:
    """Extrait la liste des noms de fichiers médias présents dans un contenu HTML."""
    filenames = []
    if not html_content:
        return filenames

    if include_images:
        img_srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
        for s in img_srcs:
            if not s.startswith("http://") and not s.startswith("https://") and not s.startswith("data:"):
                decoded = urllib.parse.unquote(s)
                if decoded not in filenames:
                    filenames.append(decoded)

    if include_audio or include_video:
        sounds = re.findall(r'\[sound:([^\]]+)\]', html_content, re.IGNORECASE)
        for s in sounds:
            decoded = urllib.parse.unquote(s)
            if decoded not in filenames:
                filenames.append(decoded)

    return filenames


def find_actual_media_path(media_dir: str, filename: str) -> Optional[str]:
    """Résout le chemin réel du fichier média en gérant encodages UTF-8 / NFC / NFD, latin-1 et casse."""
    if not media_dir or not filename:
        return None

    # 1. Chemin direct
    direct_path = os.path.join(media_dir, filename)
    if os.path.isfile(direct_path):
        return direct_path

    # 2. Décodage URL
    unquoted = urllib.parse.unquote(filename)
    p = os.path.join(media_dir, unquoted)
    if os.path.isfile(p):
        return p

    # 3. Normalisation Unicode (NFC / NFD)
    nfc = unicodedata.normalize("NFC", unquoted)
    p = os.path.join(media_dir, nfc)
    if os.path.isfile(p):
        return p

    nfd = unicodedata.normalize("NFD", unquoted)
    p = os.path.join(media_dir, nfd)
    if os.path.isfile(p):
        return p

    # 4. Encodage latin1 / cp1252
    try:
        raw_bytes = unquoted.encode("latin1")
        for enc in ("utf-8", "cp1252", "iso-8859-1"):
            try:
                dec = raw_bytes.decode(enc)
                p = os.path.join(media_dir, dec)
                if os.path.isfile(p):
                    return p
            except Exception:
                pass
    except Exception:
        pass

    # 5. Recherche avec préfixe si caractères accentués tronqués
    try:
        base_prefix = filename[:15] if len(filename) > 15 else filename
        for candidate in os.listdir(media_dir):
            if candidate.startswith(base_prefix):
                p = os.path.join(media_dir, candidate)
                if os.path.isfile(p):
                    return p
    except Exception:
        pass

    return None


def merge_metadata(primary: MediaMetadata, secondary: Optional[MediaMetadata]) -> MediaMetadata:
    """Fusionne deux objets de métadonnées en privilégiant les valeurs non nulles du premier."""
    if not secondary:
        return primary

    merged = MediaMetadata(
        filename=primary.filename or secondary.filename,
        author=primary.author or secondary.author,
        year=primary.year or secondary.year,
        title=primary.title or secondary.title,
        source=primary.source or secondary.source,
        source_url=primary.source_url or secondary.source_url,
        doi=primary.doi or secondary.doi,
        license=primary.license or secondary.license,
        copyright=primary.copyright or secondary.copyright,
        description=primary.description or secondary.description,
        raw_meta={**secondary.raw_meta, **primary.raw_meta}
    )
    return merged


class MediaReferenceProcessor:
    """Processeur de cartes et paquets pour l'extraction de références (fichiers locaux et web)."""

    def __init__(self, media_dir: str, config: Optional[Dict[str, Any]] = None):
        self.media_dir = media_dir
        self.config = config or get_config()
        self.field_mapper = FieldMapper(self.config.get("custom_mappings", {}))
        self.formatter = ReferenceFormatter(
            template=self.config.get("custom_template") or None,
            style=self.config.get("citation_style", "vancouver")
        )
        self.online_enrichment = self.config.get("online_enrichment", True)

    def create_note_snapshot(self, note: Any, notetype: Dict[str, Any]) -> Optional[NoteSnapshot]:
        """Crée un instantané autonome de la note pour traitement hors-base."""
        nid = getattr(note, "id", 0)
        target_field = self.field_mapper.get_reference_field(notetype)
        if not target_field:
            return None

        candidate_fields = self.field_mapper.get_media_candidate_fields(notetype, target_field)

        fields: Dict[str, str] = {}
        if hasattr(note, "keys") and callable(note.keys):
            for k in note.keys():
                fields[k] = str(note[k] or "")
        elif isinstance(note, dict):
            for k, v in note.items():
                fields[k] = str(v or "")

        tags = list(getattr(note, "tags", [])) if hasattr(note, "tags") else []

        return NoteSnapshot(
            nid=nid,
            target_field=target_field,
            candidate_fields=candidate_fields,
            fields=fields,
            tags=tags
        )

    def process_snapshot(self, snapshot: NoteSnapshot) -> NoteProcessResult:
        """
        Traite un instantané de note en mémoire SANS aucun accès à la collection Anki.
        Totalement thread-safe et non bloquant pour le reviewer.
        """
        nid = snapshot.nid
        target_field = snapshot.target_field
        old_value = snapshot.fields.get(target_field, "")

        # 1. Extraire métadonnées du HTML
        html_meta_by_filename: Dict[str, MediaMetadata] = {}
        for fld in snapshot.candidate_fields:
            content = snapshot.fields.get(fld, "")
            if content:
                context_metas = extract_html_context_metadata(content, enable_online=self.online_enrichment)
                for cm in context_metas:
                    if cm.filename not in html_meta_by_filename:
                        html_meta_by_filename[cm.filename] = cm
                    else:
                        html_meta_by_filename[cm.filename] = merge_metadata(cm, html_meta_by_filename[cm.filename])

        # 2. Trouver tous les fichiers médias locaux
        all_media_files = []
        for fld in snapshot.candidate_fields:
            content = snapshot.fields.get(fld, "")
            if content:
                files = extract_media_filenames(
                    content,
                    include_images=self.config.get("include_images", True),
                    include_audio=self.config.get("include_audio", False),
                    include_video=self.config.get("include_video", False)
                )
                for f in files:
                    if f not in all_media_files:
                        all_media_files.append(f)

        # 3. Combiner métadonnées
        extracted_meta: List[MediaMetadata] = []
        processed_filenames = set()
        all_media_count = len(all_media_files)
        local_meta_count = 0

        for filename in all_media_files:
            processed_filenames.add(filename)
            html_meta = html_meta_by_filename.get(filename)

            media_path = find_actual_media_path(self.media_dir, filename)
            local_meta = extract_media_metadata(media_path) if media_path else None

            if local_meta and html_meta:
                combined = merge_metadata(local_meta, html_meta)
            elif local_meta:
                combined = local_meta
            elif html_meta:
                combined = html_meta
            else:
                combined = MediaMetadata(filename=filename)

            if combined.has_useful_info():
                local_meta_count += 1
                extracted_meta.append(combined)

        for filename, h_meta in html_meta_by_filename.items():
            if filename not in processed_filenames:
                all_media_count += 1
                if h_meta.has_useful_info():
                    local_meta_count += 1
                    extracted_meta.append(h_meta)

        mode = self.config.get("mode", "append")
        is_old_empty = not old_value or old_value.strip() == "" or old_value.strip() == "<br>"

        # Cas 1 : Des médias sont présents mais aucune source n'a pu être trouvée
        if all_media_count > 0 and not extracted_meta:
            tag_missing = self.config.get("tag_missing_source", "").strip() or None
            flag_missing = int(self.config.get("flag_missing_source", 0))
            mention_missing = self.config.get("mention_missing_source", "").strip()

            new_value = old_value
            modified = False

            if mention_missing:
                if mode == "if_empty":
                    if is_old_empty:
                        new_value = mention_missing
                elif mode == "overwrite":
                    new_value = mention_missing
                else:  # "append"
                    if is_old_empty:
                        new_value = mention_missing
                    else:
                        new_value = f"{old_value}<br>{mention_missing}"
                modified = (new_value != old_value)

            return NoteProcessResult(
                nid=nid,
                modified=modified,
                target_field=target_field,
                old_value=old_value,
                new_value=new_value,
                media_extracted=[],
                all_media_count=all_media_count,
                local_meta_count=local_meta_count,
                has_missing_source=True,
                tag_to_add=tag_missing,
                flag_to_set=flag_missing
            )

        if not extracted_meta:
            return NoteProcessResult(
                nid=nid,
                modified=False,
                target_field=target_field,
                old_value=old_value,
                new_value=old_value,
                media_extracted=[],
                all_media_count=all_media_count,
                local_meta_count=local_meta_count,
                has_missing_source=False
            )

        # Cas 2 : Des références ont été trouvées
        formatted_ref = self.formatter.format_multiple(extracted_meta, existing_content=old_value)
        if not formatted_ref:
            return NoteProcessResult(
                nid=nid,
                modified=False,
                target_field=target_field,
                old_value=old_value,
                new_value=old_value,
                media_extracted=extracted_meta,
                all_media_count=all_media_count,
                local_meta_count=local_meta_count,
                has_missing_source=False
            )

        new_value = old_value
        if mode == "if_empty":
            if is_old_empty:
                new_value = formatted_ref
            else:
                return NoteProcessResult(
                    nid=nid,
                    modified=False,
                    target_field=target_field,
                    old_value=old_value,
                    new_value=old_value,
                    media_extracted=extracted_meta,
                    all_media_count=all_media_count,
                    local_meta_count=local_meta_count,
                    has_missing_source=False
                )
        elif mode == "overwrite":
            new_value = formatted_ref
        else:  # "append"
            if is_old_empty:
                new_value = formatted_ref
            else:
                new_value = f"{old_value}<br>{formatted_ref}"

        modified = (new_value != old_value)
        tag_modified = self.config.get("tag_modified_cards", "").strip() or None

        return NoteProcessResult(
            nid=nid,
            modified=modified,
            target_field=target_field,
            old_value=old_value,
            new_value=new_value,
            media_extracted=extracted_meta,
            all_media_count=all_media_count,
            local_meta_count=local_meta_count,
            has_missing_source=False,
            tag_to_add=tag_modified if modified else None
        )

    def process_note_data(self, note: Any, notetype: Dict[str, Any], preview_only: bool = False) -> NoteProcessResult:
        """Compatibilité unitaire pour l'éditeur."""
        snap = self.create_note_snapshot(note, notetype)
        if not snap:
            nid = getattr(note, "id", 0)
            return NoteProcessResult(
                nid=nid,
                modified=False,
                target_field=None,
                old_value="",
                new_value="",
                error="Aucun champ de référence cible trouvé pour ce modèle."
            )

        res = self.process_snapshot(snap)
        if not preview_only:
            if res.modified and hasattr(note, "__setitem__") and res.target_field:
                note[res.target_field] = res.new_value
            if res.tag_to_add and hasattr(note, "add_tag"):
                note.add_tag(res.tag_to_add)

        return res

    def process_snapshots_batch(
        self,
        snapshots: List[NoteSnapshot],
        progress_callback: Optional[Callable[[int, int, str], bool]] = None
    ) -> BatchProcessStats:
        """
        Traite une liste d'instantanés de notes 100% en arrière-plan sans toucher à la collection SQLite.
        """
        global LAST_EXTRACTION_STATS
        stats = BatchProcessStats(total_notes=len(snapshots))

        for idx, snap in enumerate(snapshots):
            if progress_callback:
                should_continue = progress_callback(idx, len(snapshots), f"Note {snap.nid}")
                if not should_continue:
                    break

            try:
                res = self.process_snapshot(snap)
                stats.results.append(res)
                stats.notes_processed += 1

                stats.media_found += res.all_media_count
                stats.local_meta_found += res.local_meta_count

                if res.media_extracted:
                    stats.media_with_meta += len([m for m in res.media_extracted if m.has_useful_info()])

                if res.has_missing_source:
                    stats.notes_missing_source += 1

                if res.modified or res.tag_to_add or res.flag_to_set > 0:
                    stats.notes_modified += 1

                if res.error:
                    stats.errors.append(f"Note {snap.nid}: {res.error}")

            except Exception as e:
                stats.errors.append(f"Erreur note {snap.nid}: {str(e)}")

        LAST_EXTRACTION_STATS = stats
        return stats

    def process_notes_batch(
        self,
        col: Any,
        note_ids: List[int],
        preview_only: bool = False,
        progress_callback: Optional[Callable[[int, int, str], bool]] = None
    ) -> BatchProcessStats:
        """Wrapper de compatibilité exécutant le snapshot puis la mise à jour groupée."""
        snapshots = []
        for nid in note_ids:
            try:
                note = col.get_note(nid)
                notetype = note.note_type()
                snap = self.create_note_snapshot(note, notetype)
                if snap:
                    snapshots.append(snap)
            except Exception:
                pass

        stats = self.process_snapshots_batch(snapshots, progress_callback=progress_callback)

        if not preview_only:
            notes_to_update = []
            for res in stats.results:
                if (res.modified and res.target_field) or res.tag_to_add or res.flag_to_set > 0:
                    try:
                        note = col.get_note(res.nid)
                        if res.modified and res.target_field:
                            note[res.target_field] = res.new_value
                        if res.tag_to_add and hasattr(note, "add_tag"):
                            note.add_tag(res.tag_to_add)
                        if res.flag_to_set > 0 and hasattr(note, "card_ids"):
                            for cid in note.card_ids():
                                try:
                                    card = col.get_card(cid)
                                    card.set_user_flag(res.flag_to_set)
                                    col.update_card(card)
                                except Exception:
                                    pass
                        notes_to_update.append(note)
                    except Exception:
                        pass

            if notes_to_update:
                try:
                    if hasattr(col, "update_notes"):
                        col.update_notes(notes_to_update)
                    else:
                        for n in notes_to_update:
                            col.update_note(n)
                except Exception as e:
                    stats.errors.append(f"Erreur sauvegarde groupée: {str(e)}")

        return stats
