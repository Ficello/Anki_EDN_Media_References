# Anki EDN - Références Médias 🖼️

Addon officiel pour la suite **Anki EDN** permettant d'extraire automatiquement les informations, métadonnées, auteurs, sources et licences des médias (images, schémas, illustrations) présents dans les cartes pour renseigner le champ **Référence** (ou **Sources**).

---

## 🎯 Fonctionnalités

- **Détection universelle des types de notes EDN** :
  - Prend en charge tous les modèles du deck EDN (~10 types de notes : *Basique-EDN*, *Texte à trous-EDN*, *Syndrome-EDN*, *Sémiologie-EDN*, *Image Occlusion*, *Schéma à Remplir*, etc.).
  - Détecte automatiquement la variante du champ (`Références`, `References`, `Référence`, `Reference`, `Sources`, etc.).
  - Mappage personnalisable par modèle si nécessaire.

- **Extraction multi-formats exhaustive** :
  - **EXIF** : Tags Auteur (`Artist`, `XPAuthor`), Copyright, Titre (`ImageDescription`, `XPTitle`), Commentaires.
  - **IPTC / Photoshop** : By-line (`Author`), Crédits (`Credit`), Source (`Source`), Copyright Notice, Titre, Légende.
  - **XMP (Adobe Extensible Metadata Platform)** : Dublin Core (`dc:creator`, `dc:rights`, `dc:title`), Photoshop, Creative Commons (`cc:license`, `cc:attributionName`, `cc:attributionURL`), Rights (`xmpRights:WebStatement`).
  - **PNG Chunks** : `tEXt`, `zTXt`, `iTXt` (détection des commentaires de provenance Wikimedia Commons, LiSA UNESS, etc.).
  - **WebP, GIF & SVG** : Métadonnées et blocs XML embarqués.

- **Formatage élégant & Anti-doublons** :
  - Génération de code HTML propre avec liens cliquables.
  - Vérification intelligente des doublons : évite de rajouter une référence déjà existante dans le champ.
  - Modes d'insertion : *Ajouter à la suite*, *Remplir uniquement si vide*, *Écraser*.

- **Multiples points d'accès** :
  - **Menu partagé "Anki EDN"** : Assistant d'extraction par lot avec prévisualisation (`Ctrl+Shift+E`).
  - **Navigateur (Browser)** : Clic droit ou raccourci `Ctrl+Shift+E` sur la sélection de cartes (avec support Annuler / Undo).
  - **Éditeur de note** : Bouton dédié dans la barre d'outils (`🖼️ Réf. Médias` ou `Ctrl+Alt+R`).

---

## 🚀 Utilisation

### 1. Extraction par lot (Deck ou sélection)
1. Cliquez sur le menu **Anki EDN > 🖼️ Extraire les Références Médias...** (ou `Ctrl+Shift+E`).
2. Choisissez le paquet (par défaut le deck EDN) ou utilisez votre sélection du navigateur.
3. Cliquez sur **🔍 Prévisualiser** pour inspecter les modifications proposées.
4. Cliquez sur **⚡ Lancer l'extraction** pour appliquer.

### 2. Depuis le Navigateur Anki
1. Sélectionnez une ou plusieurs cartes.
2. Faites un clic droit > **🖼️ EDN : Extraire les références des médias** (ou `Ctrl+Shift+E`).

### 3. Depuis l'Éditeur de cartes
1. Lors de l'édition ou de l'ajout d'une carte contenant une image, cliquez sur le bouton **🖼️ Réf. Médias** dans la barre d'outils (ou `Ctrl+Alt+R`).
2. Les métadonnées trouvées sont instantanément ajoutées dans le champ Référence.

---

## ⚙️ Configuration

Ouvrez **Anki EDN > ⚙️ Paramètres Références Médias** pour :
- Personnaliser le template HTML de citation.
- Forcer un champ cible spécifique pour certains modèles de note.
- Activer/désactiver l'extraction audio ou vidéo.
- Définir un tag automatique à ajouter aux cartes modifiées (ex: `ref_ajoutee`).
