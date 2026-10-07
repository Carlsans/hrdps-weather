# hrdps-weather

Météo **100 % HRDPS** (Système à haute résolution de prévision déterministe, Environnement et Changement
climatique Canada, 2,5 km, 48 h) : carte animée (précipitations, température, vent, nuages), graphiques
synchronisés, alertes et détails heure par heure.

- **Linux** : module [waybar](https://github.com/Alexays/Waybar) + fenêtre GTK4.
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

Paquets système (exemple Arch) : `python-cairo python-gobject gtk4 python-pillow python-numpy`.

```bash
pip install --user --break-system-packages git+https://github.com/Carlsans/hrdps-weather   # ou un venv --system-site-packages
hrdps-weather config
```

Module waybar (`~/.config/waybar/config.jsonc`) :

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

Espace : lecture/pause · ←/→ : ±1 h · ↑/↓ : vitesse · 1–4 : couche de la carte · Home : maintenant · Échap : fermer.
Glissez le curseur ou survolez un graphique (en pause) pour explorer le temps.

## Données, réseau et vie privée

Aucun compte, aucune clé d'API, aucune télémétrie. Seuls deux hôtes sont contactés :

| Hôte | Pourquoi |
| --- | --- |
| `geo.weather.gc.ca` (MSC GeoMet) | séries horaires au point et grilles des cartes HRDPS |
| `tile.openstreetmap.org` | tuiles du fond de carte (9 tuiles, mises en cache sur le disque) |

Votre position n'est envoyée qu'à GeoMet, sous forme d'une petite zone autour du point (comme toute requête WMS).
Un run complet représente ~1 500 petites requêtes ; le programme ne réinterroge GeoMet que lorsqu'un nouveau run
existe. Merci de ne pas réduire ce délai.

Cache : `%LOCALAPPDATA%\hrdps-weather\Cache` (Windows) ou `~/.cache/hrdps-weather` (Linux).

## Ce que le programme ne fait pas

- aucun droit administrateur, aucun service, aucune tâche planifiée, aucune règle de pare-feu (il n'écoute sur
  aucun port) ;
- aucun démarrage automatique par défaut — l'entrée de menu *Démarrer avec Windows* est facultative et ne fait
  qu'ajouter (ou retirer) une valeur `HRDPSWeather` sous `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` ;
- aucun téléchargement ni exécution de code, aucune obfuscation, aucun accès aux fichiers hors de ses dossiers de
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
