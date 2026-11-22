import { describe, expect, it } from 'vitest';
import {
  bracket,
  formatMinutes,
  mood,
  moodBySun,
  skyColour,
  skyColourBySun,
  sunHeight,
  toMinutes,
} from '../src/lib/daylight';

describe('daylight times', () => {
  it('reads and writes clock times', () => {
    expect(toMinutes('17:30')).toBe(1050);
    expect(toMinutes('08:00')).toBe(480);
    expect(formatMinutes(1050)).toBe('17:30');
    expect(formatMinutes(485.4)).toBe('08:05');
  });

  it('finds the two baked times to blend and how far between them', () => {
    const times = [480, 720, 1050, 1200];
    expect(bracket(times, 600)).toEqual([0, 1, 0.5]);
    expect(bracket(times, 1050)).toEqual([1, 2, 1]);
    expect(bracket(times, 300)).toEqual([0, 0, 0]); // before the first
    expect(bracket(times, 1400)).toEqual([3, 3, 0]); // after the last
    expect(bracket([1050], 600)).toEqual([0, 0, 0]);
  });
});

describe('daylight by the sun', () => {
  // A November day in Vienna, as the pipeline samples it: up 20 degrees at one, down by ten past four.
  const up = (degrees: number): [number, number, number] => {
    const r = (degrees * Math.PI) / 180;
    return [0, Math.sin(r), -Math.cos(r)];
  };
  const path = [
    { minutes: 450, direction: up(2), light: [1, 1, 1] as [number, number, number] },
    { minutes: 785, direction: up(20), light: [1, 1, 1] as [number, number, number] },
    { minutes: 975, direction: up(-1), light: [0, 0, 0] as [number, number, number] },
    { minutes: 1035, direction: up(-10), light: [0, 0, 0] as [number, number, number] },
  ];

  it('reads the height of the sun between the samples of its path, and which way it is going', () => {
    expect(sunHeight(path, 450).degrees).toBeCloseTo(2);
    expect(sunHeight(path, 617.5)).toEqual({ degrees: expect.closeTo(11, 5), rising: true });
    expect(sunHeight(path, 880).rising).toBe(false);
    expect(sunHeight(path, 2000).degrees).toBeCloseTo(-10); // held past the end
  });

  it('names a winter afternoon by its sun, not by the clock of a summer day', () => {
    const name = (m: number) => {
      const { degrees, rising } = sunHeight(path, m);
      return moodBySun(m, degrees, rising);
    };
    expect(name(455)).toBe('Sunrise');
    expect(name(600)).toBe('Morning');
    expect(name(785)).toBe('Midday');
    expect(name(900)).toBe('Afternoon');
    expect(name(960)).toBe('Golden hour');
    expect(name(985)).toBe('Dusk'); // 16:25: 'Afternoon' by the clock
    expect(name(1035)).toBe('Night');
  });

  it('colours the track blue with the sun up and dark with it down', () => {
    expect(skyColourBySun(30)).toBe('rgb(168,203,233)');
    expect(skyColourBySun(0)).toBe('rgb(240,170,102)');
    expect(skyColourBySun(-20)).toBe('rgb(44,46,84)');
    expect(skyColourBySun(-7)).toBe('rgb(78,76,122)');
  });
});

describe('daylight control', () => {
  it('names the time of day', () => {
    expect([480, 720, 900, 1050, 1200, 1245].map(mood)).toEqual([
      'Morning', 'Midday', 'Afternoon', 'Evening', 'Golden hour', 'Sunset',
    ]);
  });

  it('colours the track between its stops and clamps past them', () => {
    expect(skyColour(12 * 60)).toBe('rgb(168,203,233)');
    expect(skyColour(0)).toBe('rgb(243,196,141)');
    expect(skyColour(20 * 60)).toBe('rgb(111,106,160)');
    expect(skyColour(24 * 60)).toBe('rgb(72,70,118)');
    expect(skyColour(11 * 60)).toBe('rgb(191,212,224)');
  });
});
