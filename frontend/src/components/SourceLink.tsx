import { ArrowUpRight } from 'lucide-react'
import type { Photo } from '../lib/types'

export const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, '') } catch { return '' } }

const PLATFORMS: [RegExp, string][] = [
  [/(^|\.)commons\.wikimedia\.org$/, 'Wikimedia Commons'],
  [/(^|\.)wikipedia\.org$/, 'Wikipedia'],
  [/(^|\.)instagram\.com$/, 'Instagram'],
  [/(^|\.)tiktok\.com$/, 'TikTok'],
  [/(^|\.)(youtube\.com|youtu\.be)$/, 'YouTube'],
  [/(^|\.)t\.me$/, 'Telegram'],
  [/(^|\.)mapillary\.com$/, 'Mapillary'],
  [/(^|\.)flickr\.com$/, 'Flickr'],
  [/(^|\.)vk\.com$/, 'VK'],
]

/** "Instagram @nuedukz", "YouTube · Satbayev University", "Wikimedia Commons" or the site's domain. */
export function sourceName(p: Pick<Photo, 'page_url' | 'author'>): string {
  const h = host(p.page_url)
  const platform = PLATFORMS.find(([rx]) => rx.test(h))?.[1]
  if (!platform) return h
  const by = p.author?.trim()
  if (by?.startsWith('@')) return `${platform} ${by}`
  if (platform === 'YouTube' && by) return `YouTube · ${by}`
  return platform
}

/** A quiet link to the page a photo was found on: small grey text, underline and ↗ only on hover. */
export function SourceLink({ p, className = '' }: { p: Photo; className?: string }) {
  const tip = [p.page_url, p.author && `© ${p.author}`, p.license, p.date].filter(Boolean).join('\n')
  return (
    <a href={p.page_url} target="_blank" rel="noreferrer" title={tip} onClick={(e) => e.stopPropagation()}
      className={`group/src inline-flex max-w-full min-w-0 items-center gap-0.5 text-[11px] leading-4 text-muted hover:text-brand ${className}`}>
      <span className="truncate underline-offset-2 group-hover/src:underline">{sourceName(p)}</span>
      <ArrowUpRight size={11} className="shrink-0 opacity-0 transition-opacity group-hover/src:opacity-100" />
    </a>
  )
}
