"""
Intégration dans l'éditeur de cartes Anki (bouton dans la barre d'outils et raccourci Ctrl+Alt+R).
"""

from typing import List
from aqt.editor import Editor
from aqt.utils import tooltip
from aqt import mw

from .processor import MediaReferenceProcessor
from .config import get_config
from .i18n import tr


def _get_editor_note(editor):
    """Extrait de manière sécurisée la note de l'éditeur (Editor ou NewEditor)."""
    if not editor:
        return None
    note = getattr(editor, "note", None)
    if note is not None:
        return note
    nid = getattr(editor, "nid", None)
    if nid and mw and mw.col:
        try:
            return mw.col.get_note(nid)
        except Exception:
            return None
    return None


def reload_editor_note(editor: Editor) -> None:
    """Recharge la note dans l'éditeur tout en conservant le focus utilisateur (compatible toutes versions d'Anki)."""
    if hasattr(editor, "reload_note"):
        editor.reload_note()
    elif hasattr(editor, "loadNoteKeepingFocus"):
        editor.loadNoteKeepingFocus()
    elif hasattr(editor, "load_note_keeping_focus"):
        editor.load_note_keeping_focus()
    elif hasattr(editor, "loadNote"):
        editor.loadNote()
    elif hasattr(editor, "load_note"):
        try:
            editor.load_note()
        except TypeError:
            pass


def extract_references_for_editor(editor: Editor) -> None:
    """
    Extrait et insère les références de médias pour la note en cours d'édition.
    Exécute la recherche en arrière-plan pour ne pas bloquer Anki si l'utilisateur navigue/révise.
    """
    note = _get_editor_note(editor)
    if not editor or not note or not getattr(note, "id", None):
        return

    note_id = note.id
    notetype = note.note_type()
    media_dir = mw.col.media.dir() if mw and mw.col else ""
    config = get_config()

    tooltip(tr("toast_searching_bg"), parent=editor.widget)

    def run_bg():
        processor = MediaReferenceProcessor(media_dir, config)
        res = processor.process_note_data(note, notetype, preview_only=False)
        if res.modified and mw and mw.col:
            mw.col.update_note(note)
        return res

    def on_done(future):
        try:
            res = future.result()
        except Exception:
            res = None

        if not res:
            return

        # Fenêtre hôte de l'éditeur (ex: Navigateur, Édition en cours, Ajouter)
        editor_window = None
        if editor:
            if hasattr(editor, "parentWindow") and editor.parentWindow:
                editor_window = editor.parentWindow
            elif hasattr(editor, "widget") and editor.widget:
                editor_window = editor.widget.window()

        # Si l'éditeur est toujours sur la même note, on rafraîchit l'affichage
        current_note = _get_editor_note(editor)
        if editor and current_note and current_note.id == note_id:
            reload_editor_note(editor)

        # Maintenir le focus sur la fenêtre d'édition pour éviter d'être renvoyé vers mw
        if editor_window and hasattr(editor_window, "isVisible") and editor_window.isVisible():
            if hasattr(editor_window, "activateWindow"):
                editor_window.activateWindow()
            if hasattr(editor_window, "raise_"):
                editor_window.raise_()

        target_parent = editor_window or (editor.widget if editor and hasattr(editor, "widget") else mw)

        if res.modified:
            src_name = "Source identifiée"
            if res.media_extracted:
                first_meta = res.media_extracted[0]
                src_name = first_meta.title or first_meta.source or first_meta.author or "Web"
            tooltip(tr("toast_bg_updated", source=src_name), parent=target_parent)
        elif res.media_extracted:
            tooltip(tr("toast_up_to_date"), parent=target_parent)
        else:
            tooltip(tr("toast_bg_no_result"), parent=target_parent)

    mw.taskman.run_in_background(run_bg, on_done)


def setup_editor_button(buttons: List[str], editor: Editor) -> List[str]:
    """Ajoute le bouton d'extraction de références à la barre d'outils de l'éditeur."""
    btn = editor.addButton(
        icon=None,
        cmd="extract_media_ref",
        func=extract_references_for_editor,
        label=tr("editor_button_label"),
        tip=tr("editor_button_tip"),
        keys="Ctrl+Alt+R"
    )
    buttons.append(btn)
    return buttons
