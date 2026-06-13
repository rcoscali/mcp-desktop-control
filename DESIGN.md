# Piloter une IHM par capture d'écran + clavier/souris — étude & architecture

Ce document explique **pourquoi** et **comment** un agent (Claude Code) peut
piloter l'interface graphique d'un système, ce qu'il faut **ajouter** pour y
parvenir, et **comment le serveur MCP de ce dépôt** implémente cette capacité.
Le `README.md` couvre l'installation et l'usage ; ce document couvre la
conception.

> Périmètre : **Windows, macOS et Linux** (X11 + Wayland). Les spécificités OS sont
> isolées dans `backends.py` ; `server.py` est la surface d'outils commune.

---

## 1. Le besoin

Permettre à l'agent d'**observer** un écran (captures) et d'**agir** dessus
(souris, clavier) pour conduire une application graphique : ouvrir un menu,
remplir un formulaire, cliquer un bouton, lire un résultat, etc.

## 2. Ce que l'agent a / ce qui manque

- **Il a** : voir des images (il peut analyser un PNG), exécuter des commandes,
  et appeler des **outils** qu'on lui déclare. Il est entraîné au *« computer
  use »* : à partir d'une capture, raisonner puis émettre des actions
  (cliquer en (x,y), taper, raccourci…).
- **Il n'a pas** : d'outil intégré de capture d'écran ni de contrôle
  clavier/souris. Ces capacités doivent être **fournies** comme outils.

## 3. Principe : la boucle perception → action

```
capture écran ──▶ l'agent l'analyse ──▶ il décide une action ──▶ exécution (souris/clavier)
        ▲                                                               │
        └────────────────────  nouvelle capture  ◀──────────────────────┘
```

Chaque pas est un aller-retour *capture → décision → action*. Le rythme est
« humain », pas celui d'une macro instantanée.

## 4. Deux routes d'intégration

### Route A — serveur MCP (retenue)
Un serveur **MCP** expose des outils que l'agent appelle nativement, et la
**capture lui est renvoyée directement en image**. C'est l'approche propre,
robuste et réutilisable — c'est ce que ce dépôt implémente.

### Route B — scripts + shell (prototype rapide)
Sans MCP : des scripts Windows (capture vers un PNG, actions via `pyautogui` /
AutoHotkey / `nircmd`) lancés depuis le shell ; l'agent *voit* en ouvrant le
PNG. Opérationnel vite, mais plus de latence et pilotage « à la vue ».

**Pourquoi A plutôt que B** : outils de première classe, capture renvoyée en
image inline (pas d'aller-retour fichier), surface réutilisable, et possibilité
d'ajouter l'accessibilité (UI Automation) proprement.

## 5. Composants par plateforme

Cadre commun (les deux OS) : **MCP Python SDK (FastMCP)** pour exposer les
outils et renvoyer l'image, transport **stdio** ou **SSE/HTTP**.

**Windows** :

| Rôle | Choix |
|---|---|
| Capture + souris + clavier | **`pyautogui`** |
| Accessibilité (cibler par nom) | **`pywinauto`** (UI Automation) |

**macOS** :

| Rôle | Choix |
|---|---|
| Capture + souris + clavier | **`pyautogui`** |
| Accessibilité (cibler par nom) | non implémenté (pour l'instant) |

**Linux** — le grand embranchement est **X11 vs Wayland** :

| Rôle | X11 | Wayland |
|---|---|---|
| Capture | **`mss`** (sans `scrot`) | **`grim`** / `gnome-screenshot` / `spectacle` |
| Souris + clavier | **`pyautogui`** (python-xlib) | **`ydotool`** (best-effort) |
| Accessibilité | **AT-SPI** (`pyatspi`) | **AT-SPI** (`pyatspi`) |

> **Wayland bloque l'injection d'entrées par conception.** Le chemin Wayland est
> *best-effort* : `drag`/`scroll` non supportés via `ydotool`, touches limitées
> à un keymap courant, et `ydotool` exige `ydotoold` + accès `uinput`. **Préférer
> une session X11**, ou s'appuyer sur AT-SPI (`ui_tree`/`ui_click`).

## 6. Fiabilité : vision + accessibilité

La vision seule suffit mais le **clic au pixel est fragile** (DPI/scaling,
fenêtres qui bougent). Chaque OS offre une couche d'accessibilité pour cibler un
contrôle par **nom/rôle** et obtenir son rectangle exact :
- **Windows** : UI Automation (`pywinauto`) ;
- **Linux** : **AT-SPI** (`pyatspi`), pour les applis accessibles (GTK/Qt) ;
- **macOS** : non implémenté actuellement dans ce dépôt.

Le combo gagnant : **capture** (l'agent comprend l'écran) **+ accessibilité**
(actionnement robuste). Exposé par les outils `ui_tree` / `ui_click`, qui
appellent UIA ou AT-SPI selon la plateforme.

## 7. Particularité WSL → Windows

L'agent peut tourner dans **WSL (Linux)**, pas sur le bureau Windows. Le serveur
doit donc **atteindre l'hôte Windows** :
- **stdio** : pointer la commande sur `python.exe` ⇒ le SDK lance le **Python
  Windows** via l'interop, qui pilote le bureau Windows ;
- **SSE/HTTP** : lancer le serveur **nativement sur Windows**, l'agent s'y
  connecte par URL (le plus découplé, idéal cible distante).

Dans tous les cas, **le serveur s'exécute comme un processus Windows** (sinon
il ne voit pas le bureau Windows).

## 8. Comment le serveur l'implémente

**Découpage** : `server.py` est **agnostique** (outils MCP, mise à l'échelle des
coordonnées, dry-run, transport) ; `backends.py` fournit les actions **brutes en
pixels réels** via `WindowsBackend` / `MacOSBackend` / `LinuxBackend`, sélectionnés par
`get_backend()`. Ajouter un OS = ajouter un backend, sans toucher aux outils.

- **Modèle de coordonnées** : la capture est mise à l'échelle (côté max ≤
  `MCP_DESKTOP_MAX_DIM`, défaut 1280) pour limiter le coût en tokens ; **toutes
  les coordonnées d'action sont dans l'espace de l'image renvoyée**, et le
  serveur les reconvertit en pixels réels (`_to_real`). `screen_size()` expose
  le mapping (taille réelle, taille image, échelle).
- **DPI awareness** : `SetProcessDpiAwareness` au démarrage, sinon les clics
  sont décalés sur écran haute densité.
- **Outils** :
  - perception — `screen_size`, `screenshot` ;
  - souris — `mouse_move`, `click`, `double_click`, `right_click`, `drag`,
    `scroll` ;
  - clavier — `type_text`, `press_key`, `hotkey` ; `wait` ;
  - UIA — `ui_tree`, `ui_click`.
- **Transports** : `stdio` par défaut, `--sse` (ou `MCP_DESKTOP_TRANSPORT=sse`)
  pour HTTP/SSE.
- **Sécurité intégrée** :
  - `pyautogui.FAILSAFE` (souris dans un coin = arrêt immédiat) ;
  - `MCP_DESKTOP_DRY_RUN=1` ⇒ les actions deviennent des **no-op journalisés**
    (la perception reste active) — pour répéter un plan sans risque ;
  - `MCP_DESKTOP_PAUSE` (délai inter-action).

## 9. Garde-fous d'exploitation

- Piloter une vraie machine est **irréversible / à effet de bord** : le client
  devrait **confirmer toute action risquée** (fermer, supprimer, valider).
- **Tester d'abord sur une VM / session jetable**, pas un poste de production.
- Commencer en **`DRY_RUN`** pour valider perception et ciblage.
- Fixer **résolution et scaling** attendus (coordonnées stables).
- Garder le **FAILSAFE** à portée de main.

## 10. Limites & pistes

- **Multi-écran** : cible l'écran principal/virtuel.
- **Accessibilité optionnelle** : si `pywinauto` (Windows) ou `pyatspi` (Linux)
  est absent, ou sur macOS (non implémenté), les outils `ui_*` le signalent et on retombe sur vision +
  coordonnées.
- **Wayland** : injection d'entrées restreinte (cf. §5) — `drag`/`scroll` non
  supportés via `ydotool`, keymap limité ; préférer X11 ou AT-SPI.
- **Latence** : un pas = une boucle capture→décision→action.
- **Pistes** : calquer exactement le **schéma de l'outil “computer use”
  d'Anthropic** (résolution normalisée) pour un rendement optimal ; script de
  fumée (`screenshot` + `screen_size`) ; gestion explicite multi-moniteurs.

## 11. Démarrage rapide

Voir `README.md`. En résumé : installer `requirements.txt` sur Windows,
enregistrer le serveur dans Claude Code (stdio ou SSE via `mcp.json.example`),
redémarrer Claude Code, puis piloter l'IHM via les outils exposés.
