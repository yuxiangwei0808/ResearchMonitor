import type { TaskStatus } from '../types'

export function humanize(value: string) {
  return value.replace(/[._-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function formatDate(value?: string | null, includeTime = false) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, includeTime
    ? { dateStyle: 'medium', timeStyle: 'short' }
    : { dateStyle: 'medium' }).format(date)
}

export function formatCalendarDate(value?: string | null) {
  if (!value) return '—'
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return formatDate(value)
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

export function relativeDate(value?: string | null) {
  if (!value) return 'No activity yet'
  const date = new Date(value)
  const seconds = Math.round((date.getTime() - Date.now()) / 1000)
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ['year', 31536000],
    ['month', 2592000],
    ['week', 604800],
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
  ]
  for (const [unit, divisor] of units) {
    if (Math.abs(seconds) >= divisor) return formatter.format(Math.round(seconds / divisor), unit)
  }
  return 'just now'
}

export const statusTone: Record<TaskStatus, string> = {
  planned: 'neutral',
  in_progress: 'blue',
  blocked: 'red',
  review: 'amber',
  done: 'green',
  dropped: 'muted',
}

export function shortPath(path: string, max = 46) {
  if (path.length <= max) return path
  const pieces = path.split('/').filter(Boolean)
  if (pieces.length < 3) return `…${path.slice(-(max - 1))}`
  return `…/${pieces.slice(-3).join('/')}`
}

export function bytes(value?: number | null) {
  if (value == null) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(1)} GB`
}
