# hrdps-weather

Météo d'**Environnement et Changement climatique Canada** : prévisions du **HRDPS** (Système à haute résolution de
prévision déterministe, 2,5 km, 48 h) et **radar météo** observé. Carte animée (précipitations, température, vent,
nuages, radar, marées) avec zoom et déplacement, graphiques synchronisés, alertes, détails heure par heure, soleil et
normales climatiques.

- **Linux** : module [waybar](https://github.com/Alexays/Waybar) (et i3blocks, polybar, i3status-rust…) + fenêtre ; binaire prêt à l'emploi.
- **Windows** : icône dans la zone de notification (température sur l'icône) + fenêtre complète au clic.
- **Android** : application avec **widget d'écran d'accueil** (APK), ou application web installable depuis le
  navigateur — **<https://carlsans.github.io/hrdps-weather/>** (aussi pour iPhone, tablette, ordinateur).

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

### Android — application avec widget (APK)

Téléchargez `hrdps-weather-android.apk` depuis la page
[Releases](https://github.com/Carlsans/hrdps-weather/releases/latest) sur le téléphone et ouvrez-le (Android
demande d'autoriser l'installation depuis le navigateur : c'est une application hors Play Store). Choisissez votre
position au premier lancement, puis ajoutez un **widget** avec les boutons en bas de l'application (ou appui long
sur l'écran d'accueil → *Widgets* → *Météo HRDPS*) ; le toucher ouvre l'application. Deux tailles, utilisables
ensemble :

- **3×2** : icône et température actuelles, ciel, ressenti, prochaine précipitation
  (« Neige vers 15h · 4,2 mm d'ici 12 h ») ;
- **4×3, détaillé** : la même chose, plus le vent, les rafales et l'humidité, les **alertes des 12 prochaines
  heures** (mêmes règles que la fenêtre de bureau : verglas, grésil, rafales, forte pluie, neige, gel, orage) et
  une **bande horaire** jusqu'à +12 h (icône, température, millimètres ou probabilité toutes les 2 heures).

- L'application est l'application web ci-dessous dans une fenêtre Android : elle se met à jour toute seule — à
  l'ouverture (changement appliqué sans rien toucher) et à chaque retour au premier plan (un bandeau propose alors de
  recharger, pour ne pas interrompre ce que vous faisiez). Pour une mise à jour déjà publiée qui n'apparaît pas :
  fermez complètement l'application (balayer dans les applications récentes) et rouvrez-la ; si cela ne suffit pas,
  le bouton **⟳ Recharger l'application** en bas de la page remplace la copie gardée pour le hors ligne **sans** toucher
  à vos données (inutile d'effacer les données d'Android). Chaque nouvelle APK fait aussi ce ménage à son premier
  lancement.
- Les widgets vérifient environ toutes les 15 minutes en arrière-plan (le minimum d'Android), quand le réseau est
  disponible, et en plus **20 secondes après le début de chaque heure** et à l'heure prévue de publication du
  prochain run du modèle (~3 h 10 après son départ). Ils ne téléchargent que si le run ou l'heure a changé ; la
  ligne « màj » indique la dernière vérification réussie. Android peut retarder ces tâches en économie d'énergie : ~30 petites requêtes GeoMet par heure avec le widget 3×2, ~170 avec le
  4×3 (valeurs au point heure par heure sur 12 h), soit quelques Mo par jour (la vérification de 15 minutes ne coûte
  qu'une petite requête).
- Permissions : Internet ; la position seulement si vous touchez *Utiliser ma position*.
- L'APK est signé (certificat SHA-256 `6a:cf:23:46:f4:8b:a0:d3:cc:74:9a:0b:d5:14:dd:9b:01:41:b7:d0:8b:68:0b:41:54:ea:db:52:72:6a:6f:6a`)
  et son empreinte est publiée avec la release (`SHA256SUMS-android.txt`, signé Ed25519 comme les autres).

### Android sans APK, iPhone, tablette, navigateur

Ouvrez **<https://carlsans.github.io/hrdps-weather/>** dans Chrome, puis menu **⋮ → Installer l'application**
(ou *Ajouter à l'écran d'accueil*) ; sur iPhone : Safari → Partager → *Sur l'écran d'accueil*. Rien à télécharger
depuis un magasin d'applications, aucune permission à accorder : la position GPS n'est demandée que si vous touchez
*Utiliser ma position* (sinon, entrez la latitude et la longitude).

C'est la même application que la fenêtre de bureau, réécrite pour le navigateur (`web/`, sans dépendance) : carte
animée avec les couches HRDPS, le radar et l'onglet **Marées** (pincer pour zoomer, double-toucher, glisser), les cinq
graphiques (toucher ou glisser horizontalement pour parcourir le temps), alertes, détails heure par heure, puis un
bandeau **Soleil et normales climatiques** avec le lien vers la page officielle d'Environnement Canada. Un champ
facultatif de la fenêtre *Position* permet de choisir la station de marées. Les données sont gardées sur
l'appareil : l'application s'ouvre hors ligne sur le dernier run téléchargé. Elle cherche un nouveau run quand elle
est ouverte (la première fois, ~1–2 minutes pour la série au point, puis ~1 minute pour les cartes) ; elle ne
télécharge rien en arrière-plan une fois fermée. Les nouvelles versions s'installent d'elles-mêmes : un bandeau
propose de recharger.

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

En bas de la fenêtre, sous les détails de l'heure choisie : le **lever et le coucher du soleil** (calculés sur
place) avec la durée du jour et son écart avec la veille, les **normales climatiques** de la station d'Environnement
Canada la plus proche (1981–2010 : normales du jour, comparaison avec les températures prévues aujourd'hui et
demain, précipitations, neige, ensoleillement et vent du mois) et un **lien** vers la page officielle des
prévisions d'Environnement Canada pour votre position (conditions, prévisions, radar, alertes).

**Carte** — molette ou boutons **+ / −** : zoom (autour du curseur) ; glisser : déplacer ; double-clic : zoom avant ;
bouton ◎ ou touche `0` : recentrer. Couches : précipitations, température, vent, nuages (prévision HRDPS) et
**radar** (observé : les 3 dernières heures, une image toutes les 6 min, pluie et neige ; il se recharge pour la zone
affichée quand vous déplacez la carte). L'onglet **🌊 Marées** remplace la carte par un panneau : niveau d'eau actuel
et tendance (monte/baisse), courbe prédite, niveaux observés et prévision tenant compte des surcotes, pleines et basses
mers annotées, prochaines marées et température de l'eau. Les données viennent du **Service hydrographique du Canada**
(Pêches et Océans Canada) pour la station la plus proche — de préférence une station permanente avec observations ;
le curseur de temps se promène sur la courbe. Sans station à moins de 120 km, l'onglet l'indique. Pour imposer une
station, ajoutez `tide_station = "03248"` (son code) dans `config.toml`.

**Temps** — espace : lecture/pause · ←/→ : ±1 h · ↑/↓ : vitesse · 1–6 : couche · +/− : zoom · Home : maintenant ·
Échap : fermer. Glissez le curseur ou survolez un graphique (en pause) pour explorer le temps. En mode radar, le
curseur parcourt les images du radar ; les graphiques restent sur l'heure choisie.

## Données, réseau et vie privée

Aucun compte, aucune clé d'API, aucune télémétrie. Seuls ces hôtes sont contactés (plus ceux des mises à jour, si vous les activez) :

| Hôte | Pourquoi |
| --- | --- |
| `geo.weather.gc.ca` (MSC GeoMet) | séries horaires au point et grilles de la carte (HRDPS), images du radar |
| `api-iwls.dfo-mpo.gc.ca` | marées (Service hydrographique du Canada) : liste des stations (gardée un mois), puis prédictions et niveaux de la station choisie, rafraîchis toutes les 20 min seulement quand l'onglet Marées est ouvert |
| `api.weather.gc.ca` | normales climatiques de la station la plus proche (une fois, puis gardées 2 mois dans le cache) |
| `tile.openstreetmap.org` | tuiles du fond de carte, **à la demande** selon la zone et le zoom affichés (mises en cache sur le disque) |
| `carlsans.github.io` | application web seulement : ses propres fichiers (GitHub Pages) |

Les liens du bas de la fenêtre ne s'ouvrent dans votre navigateur que si vous cliquez dessus. Votre position n'est envoyée qu'à GeoMet, sous forme d'une petite zone autour du point (comme toute requête WMS).
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

# application web : aucun outil de compilation, servir le dossier web/ suffit
python -m http.server 8000 --directory web                     # puis http://localhost:8000
python web/tests/make_expected.py expected.json && (cd web && EXPECTED=../expected.json node --test "tests/*.test.mjs")

# Android (JDK 17 + SDK Android) : tests du widget (comparés au Python) et APK
cd android && EXPECTED=../expected.json ./gradlew testReleaseUnitTest assembleRelease
```

Les tests web comparent la couche de données JavaScript (`web/js/hrdps.js`) au Python sur le même run : séries
dérivées, icônes, alertes, dates, textes, puis lever/coucher du soleil, normales, marées (`astro.js`, `climate.js`,
`tides.js`) ; ceux d'Android font de même pour la logique du widget
(`android/.../Forecast.kt`). Toute modification de `hrdps.py` doit être reportée dans `hrdps.js` et `Forecast.kt`.

## Attributions

- Données : Environnement et Changement climatique Canada (HRDPS), Service météorologique du Canada.
  *Contient de l'information licenciée en vertu de la [Licence du gouvernement ouvert – Canada](https://open.canada.ca/fr/licence-du-gouvernement-ouvert-canada).*
- Fond de carte : © contributeurs [OpenStreetMap](https://www.openstreetmap.org/copyright).
- Logiciel : licence MIT.
