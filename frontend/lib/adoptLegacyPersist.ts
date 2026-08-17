/**
 * One-shot copy of pre-rebrand localStorage blobs (takton-*) onto tevarn-*.
 * Zustand persist reads the canonical key at module init, so call this first.
 */
export function adoptLegacyPersist(canonical: string): void {
  if (typeof window === 'undefined') return;
  try {
    if (localStorage.getItem(canonical)) return;
    const legacy = canonical.replace(/^tevarn-/, 'takton-');
    if (legacy === canonical) return;
    const raw = localStorage.getItem(legacy);
    if (raw) localStorage.setItem(canonical, raw);
  } catch {
    /* ignore quota / private mode */
  }
}
