import { describe, expect, it } from 'vitest';

import { formatListingTitle, preferredLanguage } from '../src/lib/listing';

describe('formatListingTitle', () => {
  const flat = { level: 3, bedrooms: 2, bathrooms: 1, areaM2: 78 };

  it('reads in English', () => {
    expect(formatListingTitle(flat, 'en')).toBe('Level 3 · 2 bedrooms · 1 bathroom · 78 m²');
  });

  it('reads in German', () => {
    expect(formatListingTitle(flat, 'de')).toBe('3. Stock · 2 Schlafzimmer · 1 Bad · 78 m²');
  });

  it('uses the singular for one and the plural otherwise', () => {
    const one = { level: 1, bedrooms: 1, bathrooms: 2, areaM2: 41.6 };
    expect(formatListingTitle(one, 'en')).toBe('Level 1 · 1 bedroom · 2 bathrooms · 42 m²');
    expect(formatListingTitle(one, 'de')).toBe('1. Stock · 1 Schlafzimmer · 2 Bäder · 42 m²');
    const none = { level: 2, bedrooms: 0, bathrooms: 0, areaM2: 30 };
    expect(formatListingTitle(none, 'en')).toBe('Level 2 · 0 bedrooms · 0 bathrooms · 30 m²');
  });

  it('names the ground floor', () => {
    expect(formatListingTitle({ level: 0 }, 'en')).toBe('Ground floor');
    expect(formatListingTitle({ level: 0 }, 'de')).toBe('Erdgeschoss');
  });

  it('leaves out what the listing does not give', () => {
    expect(formatListingTitle({ bedrooms: 1, areaM2: 36 }, 'en')).toBe('1 bedroom · 36 m²');
    expect(formatListingTitle({}, 'en')).toBe('');
  });

  it('groups thousands as each language does', () => {
    expect(formatListingTitle({ areaM2: 1250 }, 'en')).toBe('1,250 m²');
    expect(formatListingTitle({ areaM2: 1250 }, 'de')).toBe('1.250 m²');
  });
});

describe('preferredLanguage', () => {
  it('is German when the browser asks for German first', () => {
    expect(preferredLanguage(['de-AT', 'en'])).toBe('de');
    expect(preferredLanguage(['en-GB', 'de'])).toBe('en');
    expect(preferredLanguage(['fr-FR'])).toBe('en');
  });
});
