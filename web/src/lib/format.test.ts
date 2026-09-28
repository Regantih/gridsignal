import { describe, expect, it } from 'vitest'
import { count, energy, hours, money, pct, power, priceMwh, slot } from './format'

describe('format', () => {
  it('formats dollars without changing the value', () => {
    expect(money(1234.5)).toBe('$1,234.50')
    expect(money(0)).toBe('$0.00')
    expect(money(-3.2)).toBe('-$3.20')
    expect(money(-0.001)).toBe('$0.00')
    expect(money(137779, false)).toBe('$137,779')
  })

  it('formats power and energy', () => {
    expect(power(171)).toBe('171 kW')
    expect(power(17.14, 1)).toBe('17.1 kW')
    expect(energy(6, 1)).toBe('6.0 kWh')
  })

  it('formats shares as percentages', () => {
    expect(pct(0.75)).toBe('75%')
    expect(pct(0.998, 1)).toBe('99.8%')
    expect(pct(0.995, 1)).toBe('99.5%')
  })

  it('formats hours at a sensible precision', () => {
    expect(hours(11.7)).toBe('11.7 h')
    expect(hours(2)).toBe('2 h')
    expect(hours(72)).toBe('3 days')
  })

  it('formats prices per MWh and counts', () => {
    expect(priceMwh(138.39)).toBe('$138.39/MWh')
    expect(count(1000)).toBe('1,000')
  })

  it('labels 15-minute slots', () => {
    expect(slot(0)).toBe('00:00')
    expect(slot(95)).toBe('23:45')
  })
})
