"""
Intégration dans le navigateur de cartes Anki (menu contextuel et raccourci Ctrl+Shift+E).
"""

from aqt.browser import Browser
from aqt.qt import QAction, QMenu
from aqt.utils import tooltip
from aqt import mw

from .gui_dialogs import BatchExtractDialog
from .processor import MediaReferenceProcessor
from .config import get_config
from .i18n import tr


def run_batch_on_selected_notes(browser: Browser) -> None:
    """Lance l'extraction directe sur les cartes sélectionnées dans le navigateur."""
    selected_cids = browser.selected_cards()
    if not selected_cids:
        return

    # Récupérer les identifiants uniques de notes
    note_ids = list({browser.col.get_card(cid).nid for cid in selected_cids})

    mw.checkpoint(tr("dialog_title_batch"))
    media_dir = mw.col.media.dir() if mw and mw.col else ""
    config = get_config()
    processor = MediaReferenceProcessor(media_dir, config)

    def _op(col):
        return processor.process_notes_batch(col, note_ids, preview_only=False)

    def _on_done(stats):
        if hasattr(browser, "model") and hasattr(browser.model, "reset"):
            browser.model.reset()
        mw.reset()
        tooltip(
            tr("toast_browser_done", mod=stats.notes_modified, total=len(note_ids)),
            parent=browser
        )

    try:
        from aqt.operations import QueryOp
        QueryOp(
            parent=browser,
            op=_op,
            success=_on_done
        ).with_progress(tr("dialog_title_batch")).run_in_background()
    except Exception:
        stats = _op(browser.col)
        _on_done(stats)


def open_dialog_for_selected_notes(browser: Browser) -> None:
    """Ouvre la boîte de dialogue avec prévisualisation sur les cartes sélectionnées."""
    selected_cids = browser.selected_cards()
    note_ids = list({browser.col.get_card(cid).nid for cid in selected_cids}) if selected_cids else None
    dlg = BatchExtractDialog(browser, initial_nids=note_ids)
    dlg.exec()


def setup_browser_menu(browser: Browser) -> None:
    """Ajoute les actions dans le menu du navigateur Anki."""
    menu_cards = browser.form.menu_Cards
    if not menu_cards:
        return

    # Action directe avec raccourci Ctrl+Shift+E
    act_direct = QAction(tr("menu_browser_extract"), browser)
    act_direct.setShortcut("Ctrl+Shift+E")
    act_direct.triggered.connect(lambda: run_batch_on_selected_notes(browser))
    menu_cards.addAction(act_direct)

    # Action avec dialogue / prévisualisation
    act_dialog = QAction(tr("menu_browser_assistant"), browser)
    act_dialog.triggered.connect(lambda: open_dialog_for_selected_notes(browser))
    menu_cards.addAction(act_dialog)

    # Ajout dans le menu contextuel clic droit
    browser.form.menu_Cards.addSeparator()
