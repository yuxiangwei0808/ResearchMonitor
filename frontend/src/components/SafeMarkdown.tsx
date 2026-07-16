import type { ReactNode } from 'react'

const inlinePattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\]\n]+\]\([^\s)]+\))/g

function safeHttpUrl(raw: string) {
  try {
    const value = new URL(raw)
    return value.protocol === 'http:' || value.protocol === 'https:' ? value.href : null
  } catch {
    return null
  }
}

function inline(value: string, keyPrefix: string): ReactNode[] {
  return value.split(inlinePattern).filter(Boolean).map((part, index) => {
    const key = `${keyPrefix}-${index}`
    if (part.startsWith('`') && part.endsWith('`')) return <code key={key}>{part.slice(1, -1)}</code>
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={key}>{part.slice(2, -2)}</strong>
    if (part.startsWith('*') && part.endsWith('*')) return <em key={key}>{part.slice(1, -1)}</em>
    const link = part.match(/^\[([^\]]+)]\(([^\s)]+)\)$/)
    if (link) {
      const href = safeHttpUrl(link[2])
      return href
        ? <a key={key} href={href} target="_blank" rel="noopener noreferrer">{link[1]}</a>
        : <span key={key}>{link[1]} ({link[2]})</span>
    }
    return part
  })
}

function isBlockStart(line: string) {
  return /^(#{1,6})\s+/.test(line)
    || /^\s*[-+]\s+/.test(line)
    || /^\s*\d+[.]\s+/.test(line)
    || /^>\s?/.test(line)
    || line.trimStart().startsWith('```')
}

export function SafeMarkdown({ value, empty = 'Nothing to preview yet.' }: { value: string; empty?: string }) {
  if (!value.trim()) return <p className="muted-copy">{empty}</p>
  const lines = value.split(/\r?\n/)
  const blocks: ReactNode[] = []
  let index = 0
  let key = 0

  while (index < lines.length) {
    const line = lines[index]
    if (!line.trim()) { index += 1; continue }
    if (line.trimStart().startsWith('```')) {
      const language = line.trim().slice(3).trim()
      const code: string[] = []
      index += 1
      while (index < lines.length && !lines[index].trimStart().startsWith('```')) {
        code.push(lines[index]); index += 1
      }
      if (index < lines.length) index += 1
      blocks.push(<pre key={key++}><code data-language={language || undefined}>{code.join('\n')}</code></pre>)
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/)
    if (heading) {
      const level = Number(heading[1].length)
      const content = inline(heading[2], `heading-${key}`)
      if (level === 1) blocks.push(<h1 key={key++}>{content}</h1>)
      else if (level === 2) blocks.push(<h2 key={key++}>{content}</h2>)
      else if (level === 3) blocks.push(<h3 key={key++}>{content}</h3>)
      else if (level === 4) blocks.push(<h4 key={key++}>{content}</h4>)
      else if (level === 5) blocks.push(<h5 key={key++}>{content}</h5>)
      else blocks.push(<h6 key={key++}>{content}</h6>)
      index += 1
      continue
    }
    const unordered = line.match(/^\s*[-+]\s+(.+)$/)
    const ordered = line.match(/^\s*\d+[.]\s+(.+)$/)
    if (unordered || ordered) {
      const items: ReactNode[] = []
      const expression = unordered ? /^\s*[-+]\s+(.+)$/ : /^\s*\d+[.]\s+(.+)$/
      while (index < lines.length) {
        const match = lines[index].match(expression)
        if (!match) break
        items.push(<li key={items.length}>{inline(match[1], `item-${key}-${items.length}`)}</li>)
        index += 1
      }
      blocks.push(unordered ? <ul key={key++}>{items}</ul> : <ol key={key++}>{items}</ol>)
      continue
    }
    if (/^>\s?/.test(line)) {
      const quoted: string[] = []
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoted.push(lines[index].replace(/^>\s?/, '')); index += 1
      }
      blocks.push(<blockquote key={key++}>{inline(quoted.join(' '), `quote-${key}`)}</blockquote>)
      continue
    }
    const paragraph = [line.trim()]
    index += 1
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines[index])) {
      paragraph.push(lines[index].trim()); index += 1
    }
    blocks.push(<p key={key++}>{inline(paragraph.join(' '), `paragraph-${key}`)}</p>)
  }

  return <div className="safe-markdown">{blocks}</div>
}
