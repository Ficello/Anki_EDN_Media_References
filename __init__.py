"""
Addon Anki : Références de Médias & Citations Universitaires.
Extraction automatique des métadonnées (EXIF/IPTC/XMP/PNG/SVG) et enrichissement web académique
(Crossref DOI, Wikimedia Commons, balises scientifiques, signatures d'auteurs).
Accessible via le menu Outils et compatible avec tous types de notes.
"""

from aqt import mw, gui_hooks
from aqt.qt import QAction

from .gui_dialogs import BatchExtractDialog, SettingsDialog, show_last_stats_dialog
from .editor_integration import setup_editor_button
from .browser_integration import setup_browser_menu
from .i18n import tr


def open_batch_dialog():
    """Ouvre la boîte de dialogue principale d'extraction de références."""
    dlg = BatchExtractDialog(mw)
    dlg.exec()


def open_settings_dialog():
    """Ouvre la boîte de dialogue des paramètres."""
    dlg = SettingsDialog(mw)
    dlg.exec()


def init_tools_menu():
    """Ajoute les actions dans le menu standard 'Outils' d'Anki."""
    if not mw or not hasattr(mw, "form") or not hasattr(mw.form, "menuTools"):
        return

    # Action principale d'extraction
    act_extract = QAction(tr("menu_extract"), mw)
    act_extract.triggered.connect(open_batch_dialog)
    mw.form.menuTools.addAction(act_extract)

    # Action pour consulter à tout moment les dernières statistiques
    act_stats = QAction(tr("menu_last_stats"), mw)
    act_stats.triggered.connect(lambda: show_last_stats_dialog(parent=mw))
    mw.form.menuTools.addAction(act_stats)


# Enregistrement du callback de configuration natif Anki (Outils > Extensions > Config)
if mw and hasattr(mw, "addonManager"):
    mw.addonManager.setConfigAction(__name__, open_settings_dialog)

# Hooks d'initialisation
gui_hooks.main_window_did_init.append(init_tools_menu)
gui_hooks.editor_did_init_buttons.append(setup_editor_button)
gui_hooks.browser_menus_did_init.append(setup_browser_menu)
