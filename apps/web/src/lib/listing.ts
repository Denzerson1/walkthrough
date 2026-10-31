/** The line that names a flat on its title screen: level, rooms, size. */

import type { Listing } from './manifest';

export type Language = 'en' | 'de';

/** The viewer's language: German where the browser asks for it, else English. */
export function preferredLanguage(languages: readonly string[] = navigator.languages): Language {
  return languages.find((l) => /^(de|en)\b/i.test(l))?.toLowerCase().startsWith('de') ? 'de' : 'en';
}

/**
 * "Level 3 · 2 bedrooms · 1 bathroom · 78 m²", or in German
 * "3. Stock · 2 Schlafzimmer · 1 Bad · 78 m²". Parts the listing does not
 * give are left out.
 */
export function formatListingTitle(listing: Listing, language: Language = 'en'): string {
  const { level, bedrooms, bathrooms, areaM2 } = listing;
  const de = language === 'de';
  const parts: string[] = [];
  if (level !== undefined && level !== null) {
    if (level === 0) parts.push(de ? 'Erdgeschoss' : 'Ground floor');
    else parts.push(de ? `${level}. Stock` : `Level ${level}`);
  }
  if (bedrooms !== undefined && bedrooms !== null) {
    parts.push(de ? `${bedrooms} Schlafzimmer` : `${bedrooms} ${bedrooms === 1 ? 'bedroom' : 'bedrooms'}`);
  }
  if (bathrooms !== undefined && bathrooms !== null) {
    parts.push(
      de
        ? `${bathrooms} ${bathrooms === 1 ? 'Bad' : 'Bäder'}`
        : `${bathrooms} ${bathrooms === 1 ? 'bathroom' : 'bathrooms'}`,
    );
  }
  if (areaM2 !== undefined && areaM2 !== null) {
    parts.push(`${Math.round(areaM2).toLocaleString(de ? 'de-DE' : 'en-GB')} m²`);
  }
  return parts.join(' · ');
}
