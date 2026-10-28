// PROTOTYPE — throwaway. Floating bar that cycles ?variant= (← → keys too); hidden in production builds.
import { useEffect } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'

export type Variant = { key: string; name: string }

export function useVariant(variants: Variant[]) {
  const key = new URLSearchParams(location.search).get('variant') ?? variants[0].key
  return variants.find((v) => v.key === key) ?? variants[0]
}

export function PrototypeSwitcher({ variants, current, onChange }:
  { variants: Variant[]; current: Variant; onChange: (v: Variant) => void }) {
  const step = (by: number) => {
    const i = variants.findIndex((v) => v.key === current.key)
    const next = variants[(i + by + variants.length) % variants.length]
    const query = new URLSearchParams(location.search)
    query.set('variant', next.key)
    history.replaceState(null, '', `?${query}`)
    onChange(next)
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (el.closest('input, textarea, [contenteditable]')) return
      if (e.key === 'ArrowLeft') step(-1)
      if (e.key === 'ArrowRight') step(1)
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  })
  if (import.meta.env.PROD) return null
  return (
    <div className="fixed bottom-4 left-1/2 z-[100] flex -translate-x-1/2 items-center gap-1 rounded-full bg-fuchsia-600 px-2 py-1 text-sm font-medium text-white shadow-xl">
      <button onClick={() => step(-1)} className="rounded-full p-1 hover:bg-white/20"><ChevronLeft className="size-4" /></button>
      <span className="px-2">PROTOTYPE · {current.key} — {current.name}</span>
      <button onClick={() => step(1)} className="rounded-full p-1 hover:bg-white/20"><ChevronRight className="size-4" /></button>
    </div>
  )
}
