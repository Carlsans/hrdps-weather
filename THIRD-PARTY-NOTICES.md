# Third-party notices

hrdps-weather is MIT-licensed. It uses the following components (and ships them in the Windows build):

| Component | License |
| --- | --- |
| [NumPy](https://numpy.org) | BSD-3-Clause |
| [Pillow](https://python-pillow.org) | HPND (MIT-CMU) |
| [pycairo](https://pycairo.readthedocs.io) / [cairo](https://cairographics.org) | LGPL-2.1 / MPL-1.1 |
| [pystray](https://github.com/moses-palmer/pystray) | LGPL-3.0 (Windows tray icon; dynamically imported, replaceable in the onedir build) |
| [tzdata](https://pypi.org/project/tzdata/) | Apache-2.0 |
| [Python](https://www.python.org) (incl. Tcl/Tk) | PSF License |
| [PyInstaller](https://pyinstaller.org) (build only) | GPL-2.0 with the bootloader exception |
| PyGObject / GTK 4 / Pango (Linux only, system packages) | LGPL-2.1+ |

Data and map sources:

- Weather data: HRDPS forecasts and weather radar, Environment and Climate Change Canada (Meteorological Service of Canada), served by MSC GeoMet.
  Contains information licensed under the [Open Government Licence – Canada](https://open.canada.ca/en/open-government-licence-canada).
- Base map tiles: © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL), fetched with a
  descriptive User-Agent and cached on disk, in line with the OSM tile usage policy.
