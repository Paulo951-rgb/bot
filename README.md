# Story Puzzle Solver

Surveille automatiquement une source de stories, détecte une carte bancaire fictive, extrait les zones nouvellement révélées via OCR, et fournit immédiatement les informations copiables.

**Priorité absolue : STORY DISPONIBLE → INFORMATION COPIABLE.**

Le système fonctionne entièrement en local, ne transmet jamais les données détectées à un service distant, et n'invente jamais une information (règle de fiabilité : `UNKNOWN` / `PARTIAL` plutôt qu'un faux résultat).

---

## 1. Installation

### Prérequis système

- **Python 3.9+**
- **Tesseract OCR** (`tesseract-ocr`)
- **ffmpeg** (pour le traitement vidéo)

#### Windows
```powershell
# 1) Tesseract : installez via l'installateur officiel
#    https://github.com/UB-Mannheim/tesseract/wiki
#    (ou: winget install UB-Mannheim.TesseractOCR) puis ajoutez au PATH
# 2) ffmpeg : winget install Gyan.FFmpeg
# 3) Tout-en-un (dependances + fixtures + health check) :
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
# 4) Health check standalone :
powershell -ExecutionPolicy Bypass -File scripts\health_check_windows.ps1
```

Le health check affiche `[OK]/[FAIL]` pour : Python, Tesseract, FFmpeg, modules Python, OCR, Clipboard, Configuration, Stockage, et indique l'état de la source.

#### macOS
```bash
brew install tesseract ffmpeg
```

#### Debian / Ubuntu
```bash
sudo apt-get install tesseract-ocr ffmpeg
```

### Dépendances Python

```bash
pip install -r requirements.txt
```

### Vérification

```bash
npm run check
# ou
python -m story_puzzle_solver.app.cli check
```

Affiche l'état de chaque dépendance. Si quelque chose manque, un message clair indique comment le corriger.

---

## 2. Configuration

Copiez `.env.example` en `.env` et ajustez :

```ini
COMPETITION_START=22:50      # début de la fenêtre de surveillance
EXPECTED_EVENT_TIME=23:00    # heure prévue de la publication principale
COMPETITION_END=23:30        # fin de la surveillance

POLL_INTERVAL_MS=250         # intervalle d'interrogation de la source

OCR_WORKERS=2                # workers OCR parallèles
OCR_CONFIDENCE_HIGH=0.90    # seuil de confiance élevé
NOTIFY_MIN_CONFIDENCE=0.75   # seuil minimal pour notifier

SIMULATION=true              # utiliser la source simulée
DEBUG_MODE=false             # mode diagnostic (règle 59)
```

Toutes les valeurs sont configurables — aucune n'est codée en dur dans le programme (règle 5).

---

## 2b. État initial de l'énigme (règle §3 BIS)

Avant le mode compétition, renseignez les valeurs **réellement connues** dans :

```
config/puzzle_initial_state.json
```

```json
{
  "regions": {
    "region_01": {"value": "4532", "status": "KNOWN"},
    "region_02": {"value": null,   "status": "UNKNOWN"}
  }
}
```

- Seules les valeurs non-nulles sont chargées comme `KNOWN`.
- Les valeurs `null` restent `UNKNOWN` — **jamais inventées**.
- Au redémarrage, l'état persistant (`.state/puzzle_state.json`) a priorité sur l'état initial.

### Disposition physique de la carte

Le système connaît la structure de la carte (coordonnées normalisées 0..1) :

```
┌─────────────────────────────┐
│ NOM DU TITULAIRE              │  ← CARDHOLDER_NAME (haut)
│ [NOM]                        │
│ NUMÉRO DE CARTE              │
│ [XXXX XXXX XXXX XXXX]        │  ← CARD_NUMBER (milieu)
│ EXPIRATION   CVC             │
│ [XX/XX]      [XXX]           │  ← EXPIRATION_DATE + CVC (bas)
└─────────────────────────────┘
```

Les ROI sont définis dans le repère de la carte **normalisée** (1024×640), pas dans la photo. Ainsi, quelle que soit la position/orientation/échelle de la carte dans l'image, les mêmes régions sont retrouvées automatiquement après détection + correction de perspective (homographie).

---

## 3. Lancement

### Un seul commande (règle 68)

```bash
npm run start
# ou
python -m story_puzzle_solver.app.cli start --simulation
```

Le dashboard local s'ouvre sur `http://127.0.0.1:8765`.

### Raccourcis clavier (règle 36)

| Raccourci | Action |
|-----------|--------|
| `Ctrl+1` | Copier le numéro |
| `Ctrl+2` | Copier le nom |
| `Ctrl+3` | Copier l'expiration |
| `Ctrl+4` | Copier le code (CVV) |
| `Ctrl+5` | Copier tout |

---

## 4. Interface

Deux zones principales (règle 62) :

### Surveillance
État, dernière story, type (photo/vidéo), carte détectée, latence, dernières notifications.

### Saisie rapide (FAST ENTRY)
Toutes les informations détectées avec un bouton **COPIER** individuel + **COPIER TOUT**.
- `displayValue` : formaté avec espaces (`1234 5678 …`)
- `clipboardValue` : normalisé sans espaces (`12345678…`)
- Les champs partiels sont marqués `PARTIAL` (règle 37)

---

## 5. Mode simulation (règle 52)

Le système génère automatiquement des fixtures synthétiques (cartes, vidéos, révélations progressives) à partir d'un générateur de données de test. Aucune image de référence réelle n'est nécessaire.

```bash
npm run test:simulation
# ou
python -m story_puzzle_solver.app.cli run-simulation
```

Affiche le résultat de la séquence complète : détection, latence, notifications, valeur finale.

---

## 6. Test du jour J (règle 81)

```bash
npm run competition-test
# ou
python -m story_puzzle_solver.app.cli competition-test
```

Simule la chronologie complète : 22:50 → préparation → 23:00 → nouvelle publication → carte → révélation → OCR → résultat → notification → clipboard. Exécutable **avant** l'événement.

---

## 7. Tests

```bash
npm run test
# ou
python -m pytest tests/ -v
```

49 tests couvrant (règle 54-58, 80) :
- Détection de carte (normale, déplacée, inclinée, floue)
- Révélation de zone, aucune nouveauté
- Vidéo, carte brève, OCR ambigu, OCR confirmé
- Pipeline complet, decoys, déduplication
- Persistance d'état, notifications, garde de fiabilité
- Latence, clipboard, masques, valeurs partielles
- Scénario surprise aléatoire, récupération après erreur, redémarrage, benchmark, fallback Vision
- Vision honnêtement UNAVAILABLE, idempotency des notifications, état initial non-notifié, contrat AuthorizedStorySource

### Test sur images de référence (§22)

Si vous disposez des deux images de référence de la carte, placez-les dans `fixtures/reference-images/`, puis :

```bash
python -m story_puzzle_solver.app.cli reference-image-test
```

Produit : `card_detected`, `card_confidence`, `card_corners`, `normalized_card`, `roi_detection`, `ocr_results`. **N'invente pas** le contenu des images si elles sont absentes.

---

## 8. Architecture

```
story_puzzle_solver/
├── app/            # dashboard web local + CLI
├── card/           # CardDetector, CardTemplate, alignement (homographie)
├── clipboard/      # ClipboardEngine (presse-papier)
├── common/         # logging JSON, métriques (P50/P90/P95/P99), timing
├── config/         # configuration .env
├── diff/           # ImageDiffEngine (SSIM, abs diff, perceptuel)
├── fusion/         # ResultFusionEngine (fusion OCR + diff + vision)
├── media/          # détection photo/vidéo + déduplication (hash)
├── notification/   # WindowsNotificationManager
├── ocr/            # OCRProvider (tesseract), multi-variantes, OCR temporal
├── performance/    # cache, race engine
├── pipeline.py     # orchestrateur PuzzlePipeline
├── simulation/     # générateur de fixtures + cartes + vidéos
├── source/         # StorySource (Simulation + interface Autorisée)
├── state/          # PuzzleState + provenance
├── video/          # VideoFrameEngine (scan/focus/extraction 3 niveaux)
└── vision/         # VisionEngine (stub, secondaire)
```

### Pipeline

```
NEW STORY → MÉDIA → PHOTO/VIDÉO? → DÉTECTION CARTE → REDRESSEMENT
   → DIFF ∥ OCR ∥ VISION → FUSION → ÉTAT → NOTIFICATION → FAST ENTRY
```

Les opérations indépendantes (DIFF, OCR, Vision) sont parallélisées (règle 45, 47). Le OCR temporel fusionne les observations multi-frames pour un vidéo (règle 25). L'early-exort en vidéo permet de produire un résultat dès qu'une zone est révélée en haute confiance (règle 13).

---

## 9. Connecter une source réelle autorisée

L'architecture prévoit une interface `AuthorizedStorySource` (règle 7) et un
adaptateur de base `AuthorizedStorySourceAdapter`
(`story_puzzle_solver/source/authorized_adapter.py`). La source réelle doit
uniquement utiliser un accès légitime et autorisé. **Aucun** contournement
d'authentification, vol de cookies, CAPTCHA bypass, anti-bot bypass, ou accès à
du contenu privé n'est implémenté ni toléré (règle 7, §24).

Deux sources réelles sont déjà implémentées (branchees dans le CLI via `--source`) :

### Option 1 — Dossier surveillé (RECOMMANDÉE, la plus fiable)

Tu sauvegardes manuellement l'image/vidéo de la story (que tu regardes
légitimement) dans un dossier ; le logiciel la détecte et l'analyse
immédiatement.

```bash
# 1) créer le dossier surveillé
mkdir -p fixtures/watch

# 2) lancer en mode dossier
npm run start:folder
# ou : python -m story_puzzle_solver.app.cli start --source folder
# ou avec un dossier perso : WATCH_DIR=C:\stories npm run start:folder

# 3) déposer une image/vidéo de story dans fixtures/watch/
# 4) le logiciel détecte la carte, OCR, notification, COPIER disponible
```

Avantages : zéro problème légal, zéro anti-bot, fonctionne à coup sûr, latence
minimale. Le dashboard affiche `SOURCE = AUTHORIZED`.

### Option 2 — Automatisation de navigateur (ton propre compte)

Playwright pilote **ton** navigateur avec **ta** session Snapchat connectée.
Au premier lancement, connecte-toi manuellement ; le profil persistant
(`.browser-profile`) mémorise la session.

```bash
# 1) installer Playwright (une fois)
pip install playwright
playwright install chromium

# 2) définir l'utilisateur à surveiller + lancer
SNAP_TARGET_USERNAME=benoit npm run start:browser
# ou : SNAP_TARGET_USERNAME=benoit python -m story_puzzle_solver.app.cli start --source browser
```

Au premier lancement, connecte-toi à Snapchat dans la fenêtre, puis relance.
Le logiciel capture des captures d'écran périodiques des stories en lecture ;
le pipeline déduplique par hash de contenu, donc seuls les visuels réellement
nouveaux déclenchent l'analyse.

⚠️ **Limitations** : l'interface web de Snapchat est fragile (sélecteurs
best-effort, anti-automatisation possible). Si le DOM casse, bascule sur
l'Option 1 (dossier). Aucun CAPTCHA bypass / anti-bot bypass n'est implémenté.

### Brancher ta propre source

Sous-classe `AuthorizedStorySourceAdapter` et implémente `authorize()`,
`_poll_impl()` et `_get_media_impl()`. Le dashboard affichera `SOURCE = AUTHORIZED`.
Tant qu'aucune source n'est branchée, il affiche honnêtement `SIMULATION` ou
`DISCONNECTED` — jamais `CONNECTED` sans une connexion réelle (règle 24).

> **Important :** Snapchat n'a pas d'API publique pour lire les stories d'un
> autre utilisateur. L'Option 1 (dossier) reste le plan le plus fiable pour le
> soir de l'événement.

---

## 10. Dépannage

| Symptôme | Solution |
|----------|----------|
| `❌ tesseract manquant` | Installez `tesseract-ocr` (voir §1) |
| `❌ Module X manquant` | `pip install -r requirements.txt` |
| OCR lent / timeout | Réduisez `OCR_WORKERS` à 1–2, ou `OCR_CONFIDENCE_HIGH` |
| Pas de notification | Vérifiez `NOTIFY_MIN_CONFIDENCE` et la confiance dans les logs |
| Vidéo non détectée | Vérifiez que `ffmpeg` est installé |
| Valeur partielle (`????`) | Normal : zone non encore révélée ou OCR ambigu (règle 2) |
| Vision indisponible | Normal : le pipeline continue avec OCR + diff (règle 70) |

### Mode diagnostic (règle 59)

```ini
DEBUG_MODE=true
```

Active les logs détaillés (carte détectée, quadrilatère, zones, diff, scores OCR).

### Logs

Les logs sont JSON structurés dans `.logs/` (règle 42). Les valeurs complètes des informations détectées ne sont **jamais** écrites dans les logs par défaut.

---

## 11. Robustesse (règle 50, 70)

Le système gère : perte réseau, source indisponible, média corrompu, OCR échoué, Vision indisponible, worker planté, timeout, redémarrage (récupération d'état), story dupliquée, vidéo courte/longue, carte partiellement cachée/floue, changement de perspective.

Fallsback : CPU si pas de GPU, OCR secondaire si primaire indisponible, pipeline sans Vision, résultat conservé même si notification échoue.

---

## 12. Limitations réellement présentes (état honnête)

Le logiciel n'est **pas** "fully operational" pour la surveillance réelle tant
que les points suivants ne sont pas résolus. Voici l'état réel par composant :

| Composant | État | Détail |
|-----------|------|--------|
| Simulation complète (photo, vidéo, carte, OCR, fusion, état, notif, clipboard) | ✅ Fonctionnel et testé | 49 tests, competition-test OK |
| CardDetector / homographie / ROI / diff / OCR / fusion / PuzzleState | ✅ Fonctionnel et testé | Tesseract 5.5, OpenCV |
| Persistance + redémarrage + récupération erreur | ✅ Fonctionnel et testé | |
| Notifications Windows (toast PowerShell) | ⚠️ Code écrit, NON testé sur Windows | Logique de dispatch validée sous Linux (stub). Le toast PowerShell réel doit être vérifié sur la machine cible. |
| Clipboard Windows (`Set-Clipboard`) | ⚠️ Code écrit, NON testé sur Windows | UTF-8, sans saut de ligne. Le fallback Linux est testé. |
| **AuthorizedStorySource (source réelle)** | ❌ Interface + adaptateur seulement | **Aucune implémentation réelle branchée.** Sans elle, le logiciel ne surveille que la simulation. À brancher avec un accès légitime (§9). |
| VisionEngine | ❌ Honnêtement UNAVAILABLE | Aucun backend configuré. Le pipeline fonctionne avec OCR + diff seul (règle 70). |
| Images de référence réelles | ❌ Absentes du dépôt | Le détecteur n'est validé que sur la carte synthétique. Placez vos images dans `fixtures/reference-images/` et lancez `reference-image-test` (§7). |

**Verdict :** le pipeline est complet, fiable et testé en simulation. Pour le
jour J, il faut (1) brancher une `AuthorizedStorySource` légitime, (2) valider
les notifications + clipboard sur la vraie machine Windows, et (3) idéalement
valider le détecteur sur les images de référence réelles.

---

## 13. Licence

MIT
