"""
Interfaces graphiques pour l'addon Media References :
- Boîte de dialogue de traitement par lots avec prévisualisation
- Boîte de dialogue de configuration avancée avec styles de citation Zotero et menus déroulants
- Menus déroulants insensibles au défilement de la molette (NoScrollComboBox)
- Coloration bleue garantie des paquets filtrés
"""

import html
import os
import re
from typing import Optional, List, Dict, Any

from aqt import mw
try:
    from aqt.operations import QueryOp
except ImportError:
    QueryOp = None
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QRadioButton, QButtonGroup, QCheckBox, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QProgressBar, QMessageBox, QGroupBox,
    QAbstractItemView, QColor, QBrush, QPalette, Qt, QTabWidget, QWidget,
    QStyledItemDelegate, QStyleOptionViewItem
)
from aqt.utils import tooltip, showInfo, askUser

from .config import get_config, save_config
from .processor import MediaReferenceProcessor, BatchProcessStats, NoteProcessResult
from .field_mapper import FieldMapper
from .i18n import tr


class NoScrollComboBox(QComboBox):
    """Menu déroulant insensible à la molette de souris pour éviter les modifications accidentelles."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        # Ignore l'événement de molette
        event.ignore()


class DeckItemDelegate(QStyledItemDelegate):
    """Délégué d'affichage pour colorer les paquets filtrés en bleu de manière fiable."""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        is_filtered = index.data(Qt.ItemDataRole.UserRole + 1)
        if is_filtered:
            painter.save()
            blue_color = QColor("#1e88e5")
            opt.palette.setColor(QPalette.ColorRole.Text, blue_color)
            opt.palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#90caf9"))
            super().paint(painter, opt, index)
            painter.restore()
        else:
            super().paint(painter, option, index)


def is_deck_filtered(col: Any, deck_id: int) -> bool:
    """Détecte de manière robuste si un paquet est filtré / dynamique dans Anki."""
    if not col or not deck_id:
        return False

    if hasattr(col.decks, "is_filtered"):
        try:
            return bool(col.decks.is_filtered(deck_id))
        except Exception:
            pass

    if hasattr(col.decks, "is_dyn"):
        try:
            return bool(col.decks.is_dyn(deck_id))
        except Exception:
            pass

    try:
        d_dict = col.decks.get(deck_id)
        if d_dict:
            return bool(d_dict.get("dyn"))
    except Exception:
        pass

    return False


class BatchExtractDialog(QDialog):
    """Boîte de dialogue de traitement par lot et prévisualisation des références de médias."""

    def __init__(self, parent=None, initial_nids: Optional[List[int]] = None):
        if callable(parent):
            try:
                parent = parent()
            except Exception:
                parent = mw
        if parent is not None and not isinstance(parent, QWidget):
            parent = mw
        super().__init__(parent or mw)
        self.setWindowTitle(tr("dialog_title_batch"))
        self.resize(920, 620)

        self.initial_nids = initial_nids
        self.config = get_config()
        self.media_dir = mw.col.media.dir() if mw and mw.col else ""
        self.processor = MediaReferenceProcessor(self.media_dir, self.config)
        self.is_cancelled = False
        self.cached_results: List[NoteProcessResult] = []

        self._setup_ui()
        self._load_decks()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 1. Sélection du périmètre
        scope_group = QGroupBox(tr("scope_group"))
        scope_layout = QHBoxLayout(scope_group)

        scope_layout.addWidget(QLabel(tr("deck_to_analyze")))
        self.deck_combo = NoScrollComboBox()
        self.deck_combo.setMinimumWidth(280)
        self.deck_combo.setItemDelegate(DeckItemDelegate(self.deck_combo))
        self.deck_combo.currentIndexChanged.connect(self._on_deck_combo_changed)
        scope_layout.addWidget(self.deck_combo)

        if self.initial_nids:
            self.use_selection_cb = QCheckBox(tr("current_selection", n=len(self.initial_nids)))
            self.use_selection_cb.setChecked(True)
            scope_layout.addWidget(self.use_selection_cb)
        else:
            self.use_selection_cb = None

        self.cb_online = QCheckBox(tr("online_enrichment"))
        self.cb_online.setChecked(self.config.get("online_enrichment", True))
        scope_layout.addWidget(self.cb_online)

        scope_layout.addStretch()
        layout.addWidget(scope_group)

        # 2. Options d'écriture
        options_group = QGroupBox(tr("writing_mode_group"))
        options_layout = QHBoxLayout(options_group)

        self.mode_group = QButtonGroup(self)
        self.rb_append = QRadioButton(tr("mode_append"))
        self.rb_if_empty = QRadioButton(tr("mode_if_empty"))
        self.rb_overwrite = QRadioButton(tr("mode_overwrite"))

        self.mode_group.addButton(self.rb_append, 1)
        self.mode_group.addButton(self.rb_if_empty, 2)
        self.mode_group.addButton(self.rb_overwrite, 3)

        current_mode = self.config.get("mode", "append")
        if current_mode == "if_empty":
            self.rb_if_empty.setChecked(True)
        elif current_mode == "overwrite":
            self.rb_overwrite.setChecked(True)
        else:
            self.rb_append.setChecked(True)

        options_layout.addWidget(self.rb_append)
        options_layout.addWidget(self.rb_if_empty)
        options_layout.addWidget(self.rb_overwrite)
        options_layout.addStretch()

        layout.addWidget(options_group)

        # 3. Tableau de prévisualisation
        table_group = QGroupBox(tr("preview_group"))
        table_layout = QVBoxLayout(table_group)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            tr("col_nid"), tr("col_model"), tr("col_field"), tr("col_old"), tr("col_new")
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        table_layout.addWidget(self.table)
        layout.addWidget(table_group)

        # 4. Barre de statut et progression
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel(tr("status_ready"))
        layout.addWidget(self.status_label)

        # 5. Boutons d'action
        btn_layout = QHBoxLayout()

        self.btn_settings = QPushButton(tr("btn_settings"))
        self.btn_settings.clicked.connect(self._open_settings)
        btn_layout.addWidget(self.btn_settings)

        btn_layout.addStretch()

        self.btn_preview = QPushButton(tr("btn_preview"))
        self.btn_preview.clicked.connect(self._run_preview)
        btn_layout.addWidget(self.btn_preview)

        self.btn_apply = QPushButton(tr("btn_apply"))
        self.btn_apply.setDefault(True)
        self.btn_apply.clicked.connect(self._run_apply)
        btn_layout.addWidget(self.btn_apply)

        self.btn_close = QPushButton(tr("btn_close"))
        self.btn_close.clicked.connect(self.close)
        btn_layout.addWidget(self.btn_close)

        layout.addLayout(btn_layout)

    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            self.config = get_config()
            self.processor = MediaReferenceProcessor(self.media_dir, self.config)

    def _on_deck_combo_changed(self, idx: int):
        if self.use_selection_cb and idx != 1:
            self.use_selection_cb.setChecked(False)

    def _load_decks(self):
        """Remplit la liste des paquets et marque les paquets filtrés pour affichage en bleu."""
        if not mw or not mw.col:
            return

        self.deck_combo.blockSignals(True)
        self.deck_combo.clear()
        self.deck_combo.addItem(tr("all_decks"), "")
        self.deck_combo.setItemData(0, False, Qt.ItemDataRole.UserRole + 1)

        decks = mw.col.decks.all_names_and_ids()
        blue_brush = QBrush(QColor("#1e88e5"))

        for d in decks:
            combo_idx = self.deck_combo.count()
            filtered = is_deck_filtered(mw.col, d.id)

            self.deck_combo.addItem(d.name, d.id)
            self.deck_combo.setItemData(combo_idx, filtered, Qt.ItemDataRole.UserRole + 1)

            if filtered:
                self.deck_combo.setItemData(combo_idx, blue_brush, Qt.ItemDataRole.ForegroundRole)

        self.deck_combo.setCurrentIndex(0)
        self.deck_combo.blockSignals(False)

    def _get_target_nids(self) -> List[int]:
        if not mw or not mw.col:
            return []

        if self.use_selection_cb and self.use_selection_cb.isChecked() and self.initial_nids:
            return self.initial_nids

        deck_data = self.deck_combo.currentData()
        if not deck_data:
            return list(mw.col.find_notes(""))
        return list(mw.col.find_notes(f'deck:"{deck_data}"'))

    def _get_current_mode(self) -> str:
        if self.rb_if_empty.isChecked():
            return "if_empty"
        elif self.rb_overwrite.isChecked():
            return "overwrite"
        return "append"

    def _run_preview(self):
        nids = self._get_target_nids()
        if not nids:
            showInfo(tr("no_cards_found"))
            return

        self.config["mode"] = self._get_current_mode()
        self.config["online_enrichment"] = self.cb_online.isChecked()
        self.processor = MediaReferenceProcessor(self.media_dir, self.config)

        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(nids))
        self.progress_bar.setValue(0)
        self.status_label.setText(tr("status_analyzing", n=len(nids)))
        self.table.setRowCount(0)

        def _bg_preview(col):
            results = []
            for nid in nids:
                try:
                    note = col.get_note(nid)
                    notetype = note.note_type()
                    res = self.processor.process_note_data(note, notetype, preview_only=True)
                    if res.modified:
                        model_name = notetype.get("name", "") if notetype else ""
                        results.append((res, model_name))
                except Exception:
                    pass
            return results

        def _on_preview_done(results):
            self.progress_bar.setVisible(False)
            self.table.setRowCount(len(results))

            for row, (res, model_name) in enumerate(results):
                self.table.setItem(row, 0, QTableWidgetItem(str(res.nid)))
                self.table.setItem(row, 1, QTableWidgetItem(model_name))
                self.table.setItem(row, 2, QTableWidgetItem(res.target_field or ""))

                clean_old = re.sub(r'<[^>]+>', ' ', res.old_value).strip()
                clean_new = re.sub(r'<[^>]+>', ' ', res.new_value).strip()

                self.table.setItem(row, 3, QTableWidgetItem(clean_old))
                self.table.setItem(row, 4, QTableWidgetItem(clean_new))

            self.status_label.setText(
                tr("status_preview_done", mod=len(results), total=len(nids))
            )

        if QueryOp and mw:
            QueryOp(
                parent=self,
                op=_bg_preview,
                success=_on_preview_done
            ).with_progress(tr("status_analyzing", n=len(nids))).run_in_background()
        else:
            col = mw.col if mw and hasattr(mw, "col") else None
            results = _bg_preview(col)
            _on_preview_done(results)

    def _run_apply(self):
        nids = self._get_target_nids()
        if not nids:
            showInfo(tr("no_cards_found"))
            return

        if not askUser(tr("confirm_apply", n=len(nids))):
            return

        self.config["mode"] = self._get_current_mode()
        self.config["online_enrichment"] = self.cb_online.isChecked()
        save_config(self.config)

        processor = MediaReferenceProcessor(self.media_dir, self.config)

        # 1. Snapshot instantané des cartes sur le thread principal (< 10 ms)
        snapshots = []
        for nid in nids:
            try:
                note = mw.col.get_note(nid)
                notetype = note.note_type()
                snap = processor.create_note_snapshot(note, notetype)
                if snap:
                    snapshots.append(snap)
            except Exception:
                pass

        if not snapshots:
            showInfo(tr("no_cards_found"))
            return

        # 2. Fermer la boîte de dialogue immédiatement pour que l'utilisateur puisse réviser
        self.accept()
        tooltip(tr("bg_batch_started", n=len(snapshots)), period=4000, parent=mw)

        # 3. Traitement 100% en arrière-plan sans verrouiller SQLite ni bloquer le reviewer
        def run_batch_bg():
            return processor.process_snapshots_batch(snapshots)

        def on_batch_done(future):
            try:
                stats = future.result()
            except Exception:
                stats = None

            if not stats:
                return

            # Sauvegarde groupée ultra-rapide (< 10 ms) sur le thread principal
            if mw and mw.col:
                try:
                    notes_to_update = []
                    for res in stats.results:
                        if (res.modified and res.target_field) or res.tag_to_add or res.flag_to_set > 0:
                            try:
                                note = mw.col.get_note(res.nid)
                                if res.modified and res.target_field:
                                    note[res.target_field] = res.new_value
                                if res.tag_to_add and hasattr(note, "add_tag"):
                                    note.add_tag(res.tag_to_add)
                                if res.flag_to_set > 0 and hasattr(note, "card_ids"):
                                    for cid in note.card_ids():
                                        try:
                                            card = mw.col.get_card(cid)
                                            card.set_user_flag(res.flag_to_set)
                                            mw.col.update_card(card)
                                        except Exception:
                                            pass
                                notes_to_update.append(note)
                            except Exception:
                                pass

                    if notes_to_update:
                        if hasattr(mw.col, "update_notes"):
                            mw.col.update_notes(notes_to_update)
                        else:
                            for n in notes_to_update:
                                mw.col.update_note(n)
                except Exception:
                    pass

            summary_msg = (
                f"<h3>{tr('stats_dialog_title')}</h3>"
                f"<p>• <b>{tr('success_analyzed', n=stats.total_notes)}</b><br>"
                f"• <b>{tr('success_updated', n=stats.notes_modified)}</b></p>"
                f"<hr>"
                f"<p><b>📊 {tr('stats_media_detected', n=stats.media_found)}</b><br>"
                f"{tr('stats_local_found', n=stats.local_meta_found)}<br>"
                f"{tr('stats_missing_source', n=stats.notes_missing_source)}<br>"
                f"<b>{tr('stats_total_refs', n=stats.media_with_meta)}</b></p>"
                f"<hr>"
                f"<p style='color: #666; font-size: 0.9em;'>{tr('undo_hint')}</p>"
            )

            is_in_review = (getattr(mw, "state", "") == "review")

            if is_in_review:
                short_msg = (
                    f"✅ Extraction terminée en arrière-plan !\n"
                    f"• {stats.notes_modified} cartes modifiées / signalées sur {stats.total_notes}\n"
                    f"• {stats.media_with_meta} références trouvées\n"
                    f"(Consultez 'Outils > Dernières statistiques' pour le bilan complet)"
                )
                tooltip(short_msg, period=7000, parent=mw)
            else:
                try:
                    if mw and mw.col:
                        mw.reset()
                except Exception:
                    pass
                showInfo(summary_msg, title=tr("stats_dialog_title"), type="info", textFormat="rich", parent=mw)

        mw.taskman.run_in_background(run_batch_bg, on_batch_done)


def show_last_stats_dialog(parent=None):
    """Affiche la boîte de dialogue des dernières statistiques d'extraction."""
    from .processor import get_last_stats
    stats = get_last_stats()
    if not stats:
        showInfo("Aucune extraction de métadonnées n'a encore été effectuée lors de cette session.", parent=parent or mw)
        return

    summary_msg = (
        f"<h3>{tr('stats_dialog_title')}</h3>"
        f"<p>• <b>{tr('success_analyzed', n=stats.total_notes)}</b><br>"
        f"• <b>{tr('success_updated', n=stats.notes_modified)}</b></p>"
        f"<hr>"
        f"<p><b>📊 {tr('stats_media_detected', n=stats.media_found)}</b><br>"
        f"{tr('stats_local_found', n=stats.local_meta_found)}<br>"
        f"{tr('stats_missing_source', n=stats.notes_missing_source)}<br>"
        f"<b>{tr('stats_total_refs', n=stats.media_with_meta)}</b></p>"
        f"<hr>"
        f"<p style='color: #666; font-size: 0.9em;'>{tr('undo_hint')}</p>"
    )
    showInfo(summary_msg, title=tr("stats_dialog_title"), type="info", textFormat="rich", parent=parent or mw)


class SettingsDialog(QDialog):
    """Boîte de dialogue de configuration pour l'addon (styles de citation Zotero, signalement et champs)."""

    def __init__(self, parent=None):
        if callable(parent):
            try:
                parent = parent()
            except Exception:
                parent = mw
        if parent is not None and not isinstance(parent, QWidget):
            parent = mw
        super().__init__(parent or mw)
        self.setWindowTitle(tr("dialog_title_settings"))
        self.resize(640, 540)
        self.config = get_config()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        tab_widget = QTabWidget()

        # Tab 1: Général & Options
        tab_general = QWidget()
        gen_layout = QVBoxLayout(tab_general)

        self.cb_online_enrichment = QCheckBox(tr("online_enrichment"))
        self.cb_online_enrichment.setChecked(self.config.get("online_enrichment", True))
        gen_layout.addWidget(self.cb_online_enrichment)

        gen_layout.addSpacing(12)
        gen_layout.addWidget(QLabel(f"<b>{tr('tag_label')}</b>"))
        self.tag_edit = QLineEdit(self.config.get("tag_modified_cards", ""))
        self.tag_edit.setPlaceholderText(tr("tag_placeholder"))
        gen_layout.addWidget(self.tag_edit)

        gen_layout.addSpacing(12)
        self.cb_images = QCheckBox(tr("include_images"))
        self.cb_images.setChecked(self.config.get("include_images", True))
        gen_layout.addWidget(self.cb_images)

        self.cb_audio = QCheckBox(tr("include_audio"))
        self.cb_audio.setChecked(self.config.get("include_audio", False))
        gen_layout.addWidget(self.cb_audio)

        gen_layout.addStretch()
        tab_widget.addTab(tab_general, tr("tab_general"))

        # Tab 2: Style de citation (type Zotero)
        tab_style = QWidget()
        style_layout = QVBoxLayout(tab_style)

        style_layout.addWidget(QLabel(f"<b>{tr('citation_style_group')}</b>"))

        self.style_button_group = QButtonGroup(self)
        self.rb_vancouver = QRadioButton(tr("style_vancouver"))
        self.rb_apa = QRadioButton(tr("style_apa"))
        self.rb_harvard = QRadioButton(tr("style_harvard"))
        self.rb_mla = QRadioButton(tr("style_mla"))
        self.rb_compact = QRadioButton(tr("style_compact"))
        self.rb_custom = QRadioButton(tr("style_custom"))

        self.style_button_group.addButton(self.rb_vancouver, 1)
        self.style_button_group.addButton(self.rb_apa, 2)
        self.style_button_group.addButton(self.rb_harvard, 3)
        self.style_button_group.addButton(self.rb_mla, 4)
        self.style_button_group.addButton(self.rb_compact, 5)
        self.style_button_group.addButton(self.rb_custom, 6)

        current_style = self.config.get("citation_style", "vancouver").lower()
        if current_style == "apa":
            self.rb_apa.setChecked(True)
        elif current_style == "harvard":
            self.rb_harvard.setChecked(True)
        elif current_style == "mla":
            self.rb_mla.setChecked(True)
        elif current_style == "compact":
            self.rb_compact.setChecked(True)
        elif current_style == "custom":
            self.rb_custom.setChecked(True)
        else:
            self.rb_vancouver.setChecked(True)

        style_layout.addWidget(self.rb_vancouver)
        style_layout.addWidget(self.rb_apa)
        style_layout.addWidget(self.rb_harvard)
        style_layout.addWidget(self.rb_mla)
        style_layout.addWidget(self.rb_compact)
        style_layout.addWidget(self.rb_custom)

        style_layout.addSpacing(10)
        style_layout.addWidget(QLabel(f"<b>{tr('custom_template_label')}</b>"))
        self.template_edit = QLineEdit(self.config.get("custom_template", ""))
        self.template_edit.setPlaceholderText(tr("custom_template_placeholder"))
        style_layout.addWidget(self.template_edit)

        style_layout.addWidget(QLabel(f"<i>{tr('variables_hint')}</i>"))
        style_layout.addStretch()
        tab_widget.addTab(tab_style, tr("tab_citation_style"))

        # Tab 3: Sources introuvables & Signalement
        tab_missing = QWidget()
        missing_layout = QVBoxLayout(tab_missing)

        missing_layout.addWidget(QLabel(f"<b>{tr('missing_source_group')}</b>"))
        missing_layout.addSpacing(6)

        # 1. Tag spécifique
        missing_layout.addWidget(QLabel(f"<b>{tr('opt_tag_missing')}</b>"))
        self.tag_missing_edit = QLineEdit(self.config.get("tag_missing_source", ""))
        self.tag_missing_edit.setPlaceholderText(tr("opt_tag_missing_ph"))
        missing_layout.addWidget(self.tag_missing_edit)

        missing_layout.addSpacing(10)

        # 2. Drapeau (Flag)
        missing_layout.addWidget(QLabel(f"<b>{tr('opt_flag_missing')}</b>"))
        self.flag_missing_combo = NoScrollComboBox()
        self.flag_missing_combo.addItem(tr("flag_none"), 0)
        self.flag_missing_combo.addItem(tr("flag_red"), 1)
        self.flag_missing_combo.addItem(tr("flag_orange"), 2)
        self.flag_missing_combo.addItem(tr("flag_green"), 3)
        self.flag_missing_combo.addItem(tr("flag_blue"), 4)
        self.flag_missing_combo.addItem(tr("flag_pink"), 5)
        self.flag_missing_combo.addItem(tr("flag_turquoise"), 6)
        self.flag_missing_combo.addItem(tr("flag_purple"), 7)

        cur_flag = int(self.config.get("flag_missing_source", 0))
        flag_idx = self.flag_missing_combo.findData(cur_flag)
        self.flag_missing_combo.setCurrentIndex(flag_idx if flag_idx >= 0 else 0)
        missing_layout.addWidget(self.flag_missing_combo)

        missing_layout.addSpacing(10)

        # 3. Mention dans le champ cible
        missing_layout.addWidget(QLabel(f"<b>{tr('opt_mention_missing')}</b>"))
        self.mention_missing_edit = QLineEdit(self.config.get("mention_missing_source", ""))
        self.mention_missing_edit.setPlaceholderText(tr("opt_mention_missing_ph"))
        missing_layout.addWidget(self.mention_missing_edit)

        missing_layout.addStretch()
        tab_widget.addTab(tab_missing, tr("tab_missing_sources"))

        # Tab 4: Correspondance des champs par modèle
        tab_mapping = QWidget()
        map_layout = QVBoxLayout(tab_mapping)

        map_layout.addWidget(QLabel(f"<b>{tr('models_fields_title')}</b>"))
        map_layout.addWidget(QLabel(tr("models_fields_hint")))

        self.mapping_table = QTableWidget(0, 3)
        self.mapping_table.setHorizontalHeaderLabels([
            tr("col_model_name"), tr("col_default_field"), tr("col_custom_field")
        ])
        self.mapping_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.mapping_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.mapping_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        mapper = FieldMapper()
        custom_mappings = self.config.get("custom_mappings", {})

        if mw and mw.col:
            notetypes = mw.col.models.all()
            self.mapping_table.setRowCount(len(notetypes))
            for row, nt in enumerate(notetypes):
                name = nt.get("name", "")
                auto_field = mapper.get_reference_field(nt) or tr("none")
                custom_field = custom_mappings.get(name, "")
                fields = [f["name"] for f in nt.get("flds", [])]

                self.mapping_table.setItem(row, 0, QTableWidgetItem(name))
                self.mapping_table.setItem(row, 1, QTableWidgetItem(auto_field))

                # Menu déroulant insensible à la molette
                combo = NoScrollComboBox()
                combo.addItem(tr("auto_detection", field=auto_field), "")
                for fname in fields:
                    combo.addItem(fname, fname)

                if custom_field and custom_field in fields:
                    idx = combo.findData(custom_field)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                else:
                    combo.setCurrentIndex(0)

                self.mapping_table.setCellWidget(row, 2, combo)

        map_layout.addWidget(self.mapping_table)
        tab_widget.addTab(tab_mapping, tr("tab_notetypes"))

        layout.addWidget(tab_widget)

        # Boutons Sauvegarder / Annuler
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_save = QPushButton(tr("btn_save"))
        self.btn_save.clicked.connect(self._save_and_close)
        btn_layout.addWidget(self.btn_save)

        self.btn_cancel = QPushButton(tr("btn_cancel"))
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        layout.addLayout(btn_layout)

    def _save_and_close(self):
        self.config["online_enrichment"] = self.cb_online_enrichment.isChecked()

        if self.rb_apa.isChecked():
            self.config["citation_style"] = "apa"
        elif self.rb_harvard.isChecked():
            self.config["citation_style"] = "harvard"
        elif self.rb_mla.isChecked():
            self.config["citation_style"] = "mla"
        elif self.rb_compact.isChecked():
            self.config["citation_style"] = "compact"
        elif self.rb_custom.isChecked():
            self.config["citation_style"] = "custom"
        else:
            self.config["citation_style"] = "vancouver"

        self.config["custom_template"] = self.template_edit.text().strip()
        self.config["tag_modified_cards"] = self.tag_edit.text().strip()
        self.config["tag_missing_source"] = self.tag_missing_edit.text().strip()
        self.config["flag_missing_source"] = self.flag_missing_combo.currentData()
        self.config["mention_missing_source"] = self.mention_missing_edit.text().strip()
        self.config["include_images"] = self.cb_images.isChecked()
        self.config["include_audio"] = self.cb_audio.isChecked()

        custom_mappings = {}
        for row in range(self.mapping_table.rowCount()):
            model_name = self.mapping_table.item(row, 0).text()
            combo = self.mapping_table.cellWidget(row, 2)
            custom_field = combo.currentData() if isinstance(combo, QComboBox) else ""
            if custom_field:
                custom_mappings[model_name] = custom_field

        self.config["custom_mappings"] = custom_mappings
        save_config(self.config)
        self.accept()
