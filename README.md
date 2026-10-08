# hrdps-weather

Météo d'**Environnement et Changement climatique Canada** : prévisions du **HRDPS** (Système à haute résolution de
prévision déterministe, 2,5 km, 48 h) et **radar météo** observé. Carte animée (précipitations, température, vent,
nuages, radar) avec zoom et déplacement, graphiques synchronisés, alertes et détails heure par heure.

- **Linux** : module [waybar](https://github.com/Alexays/Waybar) (et i3blocks, polybar, i3status-rust…) + fenêtre ; binaire prêt à l'emploi.
- **Windows** : icône dans la zone de notification (température sur l'icône) + fenêtre complète au clic.

## Installation

### Windows

**Option 1 — installateur (le plus simple).** Téléchargez `hrdps-weather-setup-x86_64.exe` depuis la page
[Releases](https://github.com/Carlsans/hrdps-weather/releases/latest), lancez-le (installation par utilisateur,
sans droits administrateur), puis la fenêtre s'ouvre et une notification indique où se trouve l'icône (Windows range les nouvelles icônes
sous la flèche **^** de la zone de notification). Clic gauche sur l'icône : fenêtre complète ; clic droit :
actualiser, *Démarrer avec Windows* — facultatif, démarre alors discrètement sans fenêtre —, quitter.
Relancer l'application alors qu'elle tourne déjà rouvre simplement sa fenêtre. Une version portable
(`hrdps-weather-windows-x86_64.zip`, à décompresser n'importe où) est aussi offerte.

**Windows vous avertira la première fois.** L'installateur n'est pas signé numériquement — un certificat est
une dépense annuelle que ce projet ne porte pas — alors SmartScreen affiche *« Windows a protégé votre
ordinateur »*. Cliquez sur **Informations complémentaires → Exécuter quand même**. Cet avertissement veut dire
« l'éditeur n'est pas reconnu », pas « ceci est connu comme nuisible ». Si vous préférez ne pas le prendre sur
parole, l'option 2 l'évite entièrement.

**Microsoft Defender peut mettre le téléchargement en quarantaine.** C'est un faux positif bien connu : le
programme est non signé (donc sans réputation de téléchargement), et le lanceur produit par PyInstaller se
retrouve aussi dans de vrais logiciels malveillants, si bien que ses octets correspondent à des signatures. Pour
limiter le risque, la version Windows est compilée en mode *onedir* (pas de décompression dans `%TEMP%`), sans
compression UPX, avec métadonnées de version et icône. Si cela arrive quand même : restaurez le fichier sous
**Sécurité Windows → Historique de protection**, et signalez-le au
[formulaire de faux positifs de Microsoft](https://www.microsoft.com/wdsi/filesubmission) — un signalement
confirmé profite à tout le monde. Chaque version publie ses empreintes SHA-256 (`SHA256SUMS.txt`) et est
construite par GitHub Actions à partir de ce dépôt, pour vérifier que ce que vous avez téléchargé est bien ce que
la CI a produit :

```powershell
Get-FileHash .\hrdps-weather-setup-x86_64.exe -Algorithm SHA256
```

**Option 2 — depuis les sources (aucun avertissement).** Installez Python 3.11+ depuis
<https://www.python.org/downloads/> (cocher « Add python.exe to PATH »), puis dans PowerShell :

```powershell
pip install git+https://github.com/Carlsans/hrdps-weather
hrdps-weather config        # crée et affiche le fichier de configuration (votre position)
hrdps-weather-tray          # lance l'icône (aucune fenêtre de console)
```

> Le premier lancement télécharge un run complet (~1 min). Ensuite, tout est mis à jour en arrière-plan à chaque
> nouveau run du modèle (4 fois par jour).

### Linux

**Option 1 — binaire (le plus simple)**, x86_64 et aarch64, construit sur Ubuntu 22.04 :

```bash
curl -fsSL https://raw.githubusercontent.com/Carlsans/hrdps-weather/main/install.sh | bash
hrdps-weather config        # fichier de configuration (votre position)
hrdps-weather popup         # ou « Météo HRDPS » dans le menu des applications
```

Le script vérifie le SHA-256 du binaire, installe dans `~/.local/bin` sans `sudo`, et se désinstalle avec
`--uninstall`. Barres d'état prises en charge — **waybar** (Sway, Hyprland, niri, river…), **i3blocks**,
**polybar**, **i3status-rust**, texte brut pour yambar/eww/etc. — et règles de fenêtre flottante pour Sway, i3,
Hyprland, niri, bspwm, KDE : voir [docs/linux.md](docs/linux.md).

**Option 2 — depuis les sources** (fenêtre GTK4 native Wayland). Paquets système (exemple Arch) :
`python-cairo python-gobject gtk4 python-pillow python-numpy` ; puis dans un venv qui voit les paquets système :

```bash
python -m venv --system-site-packages ~/.local/share/hrdps-weather/venv
~/.local/share/hrdps-weather/venv/bin/pip install git+https://github.com/Carlsans/hrdps-weather
```

Module waybar (`~/.config/waybar/config.jsonc`) — avec le binaire, remplacez le chemin par `hrdps-weather` :

```jsonc
"custom/weather": {
    "exec": "hrdps-weather waybar",
    "interval": 900,
    "return-type": "json",
    "format": "{}",
    "tooltip": true,
    "on-click": "hrdps-weather popup",
    "on-click-right": "hrdps-weather popup --kill"
}
```

## Configuration

`hrdps-weather config` affiche le chemin du fichier (`%APPDATA%\hrdps-weather\config.toml` sous Windows,
`~/.config/hrdps-weather/config.toml` sous Linux) :

```toml
latitude  = 46.8139
longitude = -71.2080
location  = "Québec"
timezone  = "America/Toronto"
```

La position doit être dans le domaine du HRDPS (la plus grande partie du Canada). Le dépôt ne contient aucune
position personnelle ; la vôtre reste dans votre fichier de configuration.

## Fenêtre

**Carte** — molette ou boutons **+ / −** : zoom (autour du curseur) ; glisser : déplacer ; double-clic : zoom avant ;
bouton ◎ ou touche `0` : recentrer. Couches : précipitations, température, vent, nuages (prévision HRDPS) et
**radar** (observé : les 3 dernières heures, une image toutes les 6 min, pluie et neige ; il se recharge pour la zone
affichée quand vous déplacez la carte).

**Temps** — espace : lecture/pause · ←/→ : ±1 h · ↑/↓ : vitesse · 1–5 : couche · +/− : zoom · Home : maintenant ·
Échap : fermer. Glissez le curseur ou survolez un graphique (en pause) pour explorer le temps. En mode radar, le
curseur parcourt les images du radar ; les graphiques restent sur l'heure choisie.

## Données, réseau et vie privée

Aucun compte, aucune clé d'API, aucune télémétrie. Seuls deux hôtes sont contactés :

| Hôte | Pourquoi |
| --- | --- |
| `geo.weather.gc.ca` (MSC GeoMet) | séries horaires au point et grilles de la carte (HRDPS), images du radar |
| `tile.openstreetmap.org` | tuiles du fond de carte, **à la demande** selon la zone et le zoom affichés (mises en cache sur le disque) |

Votre position n'est envoyée qu'à GeoMet, sous forme d'une petite zone autour du point (comme toute requête WMS).
Un run complet du HRDPS représente ~1 700 petites requêtes ; le programme ne réinterroge GeoMet que lorsqu'un nouveau
run existe (la première fois, le téléchargement des grilles de la carte prend ~3–4 min en arrière-plan ; la barre et
les graphiques sont disponibles avant). Le radar n'est chargé que lorsque vous ouvrez la couche radar (≈ 60 petites
images). Merci de ne pas réduire ces délais.

Cache : `%LOCALAPPDATA%\hrdps-weather\Cache` (Windows) ou `~/.cache/hrdps-weather` (Linux).

## Mises à jour (facultatives)

**Désactivées par défaut** : tant que vous ne les activez pas, le programme ne contacte aucun hôte autre que ceux du
tableau ci-dessus. Trois modes :

| Mode | Comportement |
| --- | --- |
| `off` | aucune vérification (défaut) |
| `notify` | une vérification par jour ; un bandeau (fenêtre : touche **U** ou clic) et une notification (icône) vous préviennent, vous installez quand vous voulez |
| `auto` | une vérification par jour, puis téléchargement, vérification et installation automatiques |

Choisir le mode : menu de l'icône → **Mises à jour** (Windows), ou `hrdps-weather update --mode notify|auto|off`, ou
`update = "auto"` dans `config.toml`. `hrdps-weather update --check` vérifie tout de suite, `--install` installe.

**Vérification stricte** : chaque version publie ses empreintes SHA-256 (`SHA256SUMS*.txt`) avec une **signature
Ed25519** (`.sig`) faite par la CI avec la clé de publication du projet ; la clé publique est intégrée au
programme. Rien n'est installé si la signature est invalide ou si l'empreinte du fichier téléchargé ne correspond
pas. Hôtes contactés quand les mises à jour sont activées : `api.github.com` (numéro de la dernière version) et
`github.com` / `objects.githubusercontent.com` (téléchargement) — pas d'autres.

**Selon le type d'installation :**

- *Windows, installateur* : l'installateur vérifié est lancé en mode silencieux (par utilisateur, sans droits
  administrateur) ; il ferme l'icône, remplace les fichiers et la relance discrètement, avec une notification.
- *Linux, binaire de `install.sh`* : le fichier est remplacé sur place ; l'ancienne version reste à côté sous
  `hrdps-weather.old`. Relancez le programme pour utiliser la nouvelle version.
- *Archive portable Windows, installation `pip`/sources, dossier non modifiable* : le programme vous prévient
  seulement et indique comment mettre à jour (il ne modifie jamais ces installations).

Le journal est dans le dossier de cache (`update.log`).

## Ce que le programme ne fait pas

- aucun droit administrateur, aucun service, aucune tâche planifiée, aucune règle de pare-feu (il n'écoute sur
  aucun port) ;
- aucun démarrage automatique par défaut — l'entrée de menu *Démarrer avec Windows* est facultative et ne fait
  qu'ajouter (ou retirer) une valeur `HRDPSWeather` sous `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` ;
- aucun téléchargement ni exécution de code **sauf si vous activez les mises à jour** (voir ci-dessus : signature et
  empreinte vérifiées avant toute installation), aucune obfuscation, aucun accès aux fichiers hors de ses dossiers de
  configuration et de cache.

Aucun éditeur ne peut garantir qu'un logiciel ne sera jamais signalé par un antivirus ; le code est entièrement
lisible ici.

## Développement

```bash
pip install -e ".[test]"
pytest
hrdps-weather selftest                   # vérification hors ligne
hrdps-weather png meteo.png rt 6         # image fixe, 6 h dans le futur
pip install -e ".[build]" && pyinstaller packaging/hrdps-weather.spec --clean --noconfirm   # Windows
```

## Attributions

- Données : Environnement et Changement climatique Canada (HRDPS), Service météorologique du Canada.
  *Contient de l'information licenciée en vertu de la [Licence du gouvernement ouvert – Canada](https://open.canada.ca/fr/licence-du-gouvernement-ouvert-canada).*
- Fond de carte : © contributeurs [OpenStreetMap](https://www.openstreetmap.org/copyright).
- Logiciel : licence MIT.
