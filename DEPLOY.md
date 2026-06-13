# Déploiement — WSL2 (orchestrateur) + Windows (exécuteur)

Procédure pour la topologie recommandée : un Claude Code dans **WSL2** délègue à
un Claude Code sur **Windows** (via le `bridge`), qui pilote le bureau et la voix
grâce aux serveurs **desktop-control** et **voice**. Le gros de l'installation est
côté Windows ; le côté WSL2 est rapide.

Prérequis supposés : **Windows 11**, **winget** et **WSL2** déjà présents.
`<you>` = ton nom d'utilisateur Windows (`echo $env:USERNAME`).

```
WSL2 : Claude (orchestrateur) ──bridge──▶ claude.exe (Windows) ──▶ desktop-control + voice ──▶ bureau/voix
```

---

## Partie 1 — Côté Windows (détaillé)

Ouvrir un **PowerShell Windows** (pas WSL).

### 1.1 Python
```powershell
winget install -e --id Python.Python.3.12
# nouveau terminal :
py --version        # 3.12.x
```

### 1.2 Claude Code (Windows)
⚠️ **Confirmer la commande exacte sur la doc officielle Anthropic** (le CLI évolue) :
- installeur natif (PowerShell) : `irm https://claude.ai/install.ps1 | iex`
- ou via npm : `winget install -e --id OpenJS.NodeJS.LTS` puis (nouveau terminal)
  `npm install -g @anthropic-ai/claude-code`

Puis authentifier le Claude Windows (compte indépendant de celui de WSL2) :
```powershell
claude            # puis /login  (ou définir $env:ANTHROPIC_API_KEY)
claude doctor     # vérifie l'installation
```

### 1.3 Récupérer le projet sur Windows
Depuis **WSL2**, copier vers un chemin Windows (plus fiable que `\\wsl$`) :
```bash
cp -r ~/Sources/mcp-desktop-control /mnt/c/Users/<you>/mcp-desktop-control
```

### 1.4 Dépendances Python (venv dédié)
```powershell
cd C:\Users\<you>\mcp-desktop-control
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt         # desktop-control : pyautogui, pillow, pywinauto
py -m pip install -r voice\requirements.txt   # voice : faster-whisper, sounddevice, pyttsx3, webrtcvad…
```
> Windows : TTS via **SAPI5** (intégré), **PortAudio** fourni par la roue
> `sounddevice`, **faster-whisper** télécharge son modèle au 1er usage. Pas
> besoin d'espeak.

### 1.5 Autoriser le micro
Réglages → **Confidentialité et sécurité → Microphone** → activer l'accès,
**y compris pour les applications de bureau**.

### 1.6 Test rapide (sur Windows)
```powershell
py voice\smoke_test.py     # TTS → enregistrement → STT → relit le résultat
```
(Le test de desktop-control se fait via le bridge en Partie 3.)

### 1.7 Déclarer les serveurs MCP au Claude **Windows**
En pointant le **python du venv** (chemins absolus) :
```powershell
$py   = "C:\Users\<you>\mcp-desktop-control\.venv\Scripts\python.exe"
$root = "C:\Users\<you>\mcp-desktop-control"
claude mcp add desktop-control -- $py "$root\server.py"
claude mcp add voice          -- $py "$root\voice\server.py"
claude mcp list     # doivent être "connected"
```

---

## Partie 2 — Côté WSL2 (le bridge)

Le dépôt est déjà présent (c'est la source). Le bridge ne dépend que de `mcp`.
```bash
cd ~/Sources/mcp-desktop-control
python3 -m pip install --user mcp        # ou dans un venv WSL

# claude.exe doit être joignable depuis WSL (interop) :
which claude.exe || echo "PATH Windows non hérité → fixer ASK_WIN_CLAUDE_BIN"

claude mcp add windows-claude-bridge -- python3 ~/Sources/mcp-desktop-control/bridge/server.py
```
Si `claude.exe` est introuvable, ajouter l'env `ASK_WIN_CLAUDE_BIN` (chemin
complet du `claude.exe`) à la config du serveur bridge.

---

## Partie 3 — Test de bout en bout

```bash
# le bridge atteint bien le Claude Windows :
python3 ~/Sources/mcp-desktop-control/bridge/ask.py "Quel est ton répertoire de travail ?" --json
```
Puis, depuis le **Claude WSL2**, demander une délégation via `ask_windows_claude`,
par ex. : « prends une capture d'écran avec desktop-control et décris-la »,
en passant `allowed_tools=["mcp__desktop-control__*","mcp__voice__*"]` et
`permission_mode="acceptEdits"` (ou `bypassPermissions` sur machine de confiance).

---

## Notes / sécurité
- **Permissions headless** : en `-p`, le Claude Windows n'agit que sur les outils
  **pré-autorisés** (`allowed_tools` / `permission_mode` passés par le bridge).
- **`bypassPermissions`** = autonomie totale → **machine de confiance / VM**
  seulement ; garder la confirmation des actions risquées.
- **DPI / résolution** : desktop-control gère le DPI ; fixer une résolution
  stable pour des coordonnées fiables.
- **Authentification** : le Claude Windows a sa propre auth (indépendante de WSL2).

## Variante : tout sur Windows
Sans WSL2, installer Python + Claude Code + le projet **sur Windows** (étapes 1.1
→ 1.7) et déclarer `desktop-control` / `voice` au Claude Windows ; le `bridge`
devient inutile. Voir aussi `WSL2.md` et `ARCHITECTURE.md`.
