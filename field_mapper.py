"""
Gestion et détection automatique des champs cibles pour les différents types de notes du deck EDN.
Règle stricte EDN : Toujours utiliser le champ Référence / Références / References s'il existe,
et NE JAMAIS écrire dans le champ Sources lorsqu'un champ de référence est présent.
"""

import unicodedata
from typing import List, Optional, Dict, Any


# Priorité absolue 1 : Champs dédiés aux références / attributions de médias
PRIMARY_REFERENCE_FIELDS = [
    "références",
    "references",
    "référence",
    "reference",
]

# Priorité secondaire : Uniquement si aucun champ de référence n'existe sur le modèle
FALLBACK_SOURCE_FIELDS = [
    "sources",
    "source",
    "remarques",
    "commentaires",
    "comments",
]


def normalize_string(s: str) -> str:
    """Supprime les accents, met en minuscules et nettoie les espaces."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).strip().lower()


class FieldMapper:
    """Détecteur et gestionnaire de champs pour les notes Anki EDN."""

    def __init__(self, custom_mappings: Optional[Dict[str, str]] = None):
        """
        :param custom_mappings: Dictionnaire optionnel associant un nom/ID de modèle à un nom de champ cible.
        """
        self.custom_mappings = custom_mappings or {}

    def get_reference_field(self, notetype: Dict[str, Any]) -> Optional[str]:
        """
        Trouve le champ de référence approprié pour un type de note donné.
        Pour le deck EDN, garantit que 'Références' / 'References' est toujours choisi
        et que 'Sources' n'est jamais écrasé si un champ Référence existe.
        """
        model_name = notetype.get("name", "")
        model_id = str(notetype.get("id", ""))
        fields = [f["name"] for f in notetype.get("flds", [])]

        # 1. Vérification dans les mappings personnalisés configurés par l'utilisateur
        if model_id in self.custom_mappings and self.custom_mappings[model_id] in fields:
            return self.custom_mappings[model_id]
        if model_name in self.custom_mappings and self.custom_mappings[model_name] in fields:
            return self.custom_mappings[model_name]

        fields_normalized = {normalize_string(fname): fname for fname in fields}

        # 2. Priorité 1 : Recherche stricte des champs de Référence (Références, References, Référence, Reference)
        for candidate in PRIMARY_REFERENCE_FIELDS:
            norm_candidate = normalize_string(candidate)
            if norm_candidate in fields_normalized:
                return fields_normalized[norm_candidate]

        for fname in fields:
            norm_f = normalize_string(fname)
            if "reference" in norm_f or "referenc" in norm_f:
                return fname

        # 3. Priorité 2 : Uniquement si aucun champ Référence n'existe sur le modèle
        for candidate in FALLBACK_SOURCE_FIELDS:
            norm_candidate = normalize_string(candidate)
            if norm_candidate in fields_normalized:
                return fields_normalized[norm_candidate]

        for fname in fields:
            norm_f = normalize_string(fname)
            if "source" in norm_f:
                return fname

        return None

    def get_media_candidate_fields(self, notetype: Dict[str, Any], target_field: Optional[str] = None) -> List[str]:
        """
        Renvoie la liste des champs susceptibles de contenir des balises <img>.
        Exclut par défaut le champ cible lui-même.
        """
        fields = [f["name"] for f in notetype.get("flds", [])]
        if target_field and target_field in fields:
            return [f for f in fields if f != target_field]
        return fields
