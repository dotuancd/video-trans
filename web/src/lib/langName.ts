const names = new Intl.DisplayNames(['vi'], { type: 'language', fallback: 'none' })

/** The language's name in Vietnamese ("vi" → "Tiếng Việt"), its code in capitals when unknown or not a BCP 47 code. */
export function langName(code: string): string {
  let name: string | undefined
  try {
    name = names.of(code)
  } catch {
    // not a BCP 47 code
  }
  return name ? name[0].toLocaleUpperCase('vi') + name.slice(1) : code.toUpperCase()
}
