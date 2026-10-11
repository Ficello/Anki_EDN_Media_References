"""
Gestion de la configuration de l'addon Media References.
"""

import json
import os
from typing import Any, Dict

ADDON_NAME = "Anki_EDN_Media_References"
DEFAULT_CONFIG = {
    "mode": "append",              # "if_empty" | "append" | "overwrite"
    "deck_filter": "",
    "citation_style": "vancouver", # "vancouver" | "apa" | "harvard" | "mla" | "compact" | "custom"
    "online_enrichment": True,     # Résolution automatique d'auteurs, revues, années via APIs officielles
    "custom_mappings": {},         # { "Notetype Name": "FieldName" }
    "custom_template": "",
    "tag_modified_cards": "",
    "tag_missing_source": "",      # Tag à ajouter si médias présents mais source introuvable (ex: "source::introuvable")
    "flag_missing_source": 0,      # Drapeau (0: Aucun, 1: Rouge, 2: Orange, 3: Vert, 4: Bleu, 5: Rose, 6: Turquoise, 7: Violet)
    "mention_missing_source": "",  # Mention dans le champ (ex: "<i>Source non renseignée</i>")
    "include_images": True,
    "include_audio": False,
    "include_video": False,
    "notify_on_complete": True
}


def get_config() -> Dict[str, Any]:
    """Charge la configuration depuis Anki ou depuis config.json en repli."""
    try:
        from aqt import mw
        if mw and mw.addonManager:
            conf = mw.addonManager.getConfig(__name__.split(".")[0])
            if conf is not None:
                merged = dict(DEFAULT_CONFIG)
                merged.update(conf)
                return merged
    except Exception:
        pass

    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                conf = json.load(f)
                merged = dict(DEFAULT_CONFIG)
                merged.update(conf)
                return merged
        except Exception:
            pass

    return dict(DEFAULT_CONFIG)


def save_config(config_data: Dict[str, Any]) -> None:
    """Enregistre la configuration dans Anki et sur le disque."""
    try:
        from aqt import mw
        if mw and mw.addonManager:
            mw.addonManager.writeConfig(__name__.split(".")[0], config_data)
            return
    except Exception:
        pass

    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
    except Exception:
        pass
