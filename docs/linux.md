# Linux : barres d'état, gestionnaires de fenêtres et options

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/Carlsans/hrdps-weather/main/install.sh | bash
# options : -s -- --systemd (minuterie de rafraîchissement)   -s -- --uninstall
```

Le script télécharge le binaire de la dernière version (x86_64 ou aarch64), **vérifie son SHA-256** contre
`SHA256SUMS-linux.txt`, l'installe dans `~/.local/bin` (sans `sudo`) et ajoute « Météo HRDPS » au menu des
applications. Relancer le script met à jour sur place. Le binaire est construit sur Ubuntu 22.04 : il tourne sur
les distributions plus récentes (testé sur Ubuntu 22.04/24.04, Debian 12, Fedora et Arch).

Le binaire utilise **Tk** pour la fenêtre (X11, ou XWayland sous Wayland) et pour le texte les polices système :
installez une police d'émojis couleur (`fonts-noto-color-emoji` / `noto-fonts-emoji`) pour voir les icônes météo.
L'installation depuis les sources (`pip`, voir le README) utilise plutôt **GTK4** (Wayland natif) si PyGObject est
présent.

Position : `hrdps-weather config` affiche le fichier à éditer (`~/.config/hrdps-weather/config.toml`).

## Barres d'état

La barre appelle `hrdps-weather` ; l'appel ne bloque jamais sur le réseau (il lit le cache et lance un
rafraîchissement en arrière-plan quand un nouveau run du modèle existe). Un intervalle de 5 à 15 min suffit.

| Barre | Commande | Format |
| --- | --- | --- |
| waybar (Sway, Hyprland, niri, river, labwc…) | `hrdps-weather waybar` | JSON + infobulle |
| i3blocks | `hrdps-weather status --format i3blocks` | 3 lignes (texte, court, couleur) |
| i3status-rust (bloc `custom`) | `hrdps-weather status --format i3status-rs` | JSON |
| polybar | `hrdps-weather status --format polybar` | texte, alerte colorée |
| yambar, eww, Quickshell, conky, tmux… | `hrdps-weather status --format plain` | une ligne de texte |

### waybar

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

Classes CSS : `weather`, et `alert` quand une alerte est active (`#custom-weather.alert { color: #fab387; }`).

### i3blocks

```ini
[weather]
command=hrdps-weather status --format i3blocks
interval=600
markup=none
```

### polybar

```ini
[module/weather]
type = custom/script
exec = hrdps-weather status --format polybar
interval = 600
click-left = hrdps-weather popup
click-right = hrdps-weather popup --kill
format-font = 2   ; une police avec émojis
```

### i3status-rust

```toml
[[block]]
block = "custom"
command = "hrdps-weather status --format i3status-rs"
json = true
interval = 600
[[block.click]]
button = "left"
cmd = "hrdps-weather popup"
```

### eww / yambar / autres

`hrdps-weather status --format plain` imprime une ligne ; par exemple `(defpoll weather :interval "10m" "hrdps-weather status --format plain")`.

## Fenêtre flottante

La fenêtre a une taille fixe (≈ 1400×924). Sur un gestionnaire en mosaïque, faites-la flotter :

| Version | Identifiant de fenêtre |
| --- | --- |
| Binaire / `popup --tk` (Tk) | classe `Hrdps-weather` (type « dialog » : la plupart des gestionnaires la font déjà flotter) |
| Source avec PyGObject (GTK4) | app-id `io.github.carlsans.hrdps-weather` |

```
# Sway / i3
for_window [class="Hrdps-weather"] floating enable
for_window [app_id="io.github.carlsans.hrdps-weather"] floating enable

# Hyprland
windowrulev2 = float, class:^(Hrdps-weather|io.github.carlsans.hrdps-weather)$
windowrulev2 = center, class:^(Hrdps-weather|io.github.carlsans.hrdps-weather)$

# niri (config.kdl)
window-rule {
    match app-id=r#"^(Hrdps-weather|io\.github\.carlsans\.hrdps-weather)$"#
    open-floating true
}

# bspwm
bspc rule -a Hrdps-weather state=floating center=on
```

KDE : *Paramètres système → Règles de fenêtre* (classe `hrdps-weather`). GNOME : rien à faire (fenêtre de type dialogue).

Sous Wayland, la version Tk passe par XWayland (niri : `xwayland-satellite`). Sans XWayland, installez depuis les sources
pour avoir la version GTK4 native.

## Rafraîchissement sans barre

Sans module de barre (fenêtre seule), le rafraîchissement a lieu à l'ouverture de la fenêtre. Pour garder le cache
à jour en continu : `install.sh --systemd` active la minuterie utilisateur `hrdps-weather-refresh.timer`
(vérification toutes les 30 min ; un téléchargement complet n'a lieu qu'à un nouveau run, 4 fois par jour).

## Désinstaller

`curl -fsSL https://raw.githubusercontent.com/Carlsans/hrdps-weather/main/install.sh | bash -s -- --uninstall`
retire le programme, l'entrée de menu et la minuterie ; la configuration et le cache sont conservés.
