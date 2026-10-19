import { describe, expect, it } from 'vitest';
import { bracket, formatMinutes, toMinutes } from '../src/lib/daylight';

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
