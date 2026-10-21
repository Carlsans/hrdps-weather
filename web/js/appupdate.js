// New versions of the Android app (APK). The web part of the app updates itself (service worker); the APK holds the
// native part (widgets, background download) and is installed by hand from the project's releases page.
export const API = "https://api.github.com/repos/Carlsans/hrdps-weather/releases/latest";
export const CHECK_EVERY = 6 * 3600e3;

/** '1.8.10' -> [1, 8, 10] */
export const parseVersion = (s) => String(s).replace(/^v/i, "").split(".").map((p) => parseInt(p, 10) || 0);

export function isNewer(a, b) {
  const x = parseVersion(a), y = parseVersion(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] || 0) - (y[i] || 0);
    if (d) return d > 0;
  }
  return false;
}

/** {version, url} when a GitHub release JSON is a newer one than `current` that already has its APK, else null. */
export function newerApk(release, current) {
  if (!release || release.draft || release.prerelease || !release.tag_name) return null;
  if (!(release.assets || []).some((a) => /\.apk$/i.test(a.name))) return null;      // still being built
  const version = release.tag_name.replace(/^v/i, "");
  return isNewer(version, current) ? { version, url: release.html_url } : null;
}
