import { useEffect } from 'react'

export const APP_NAME = 'Bayto'

/** "<page> · Bayto", or plain "Bayto" when there is no page name. */
export function formatTitle(page?: string | null): string {
  const p = page?.trim()
  return p ? `${p} · ${APP_NAME}` : APP_NAME
}

/** Sets the tab title for the current page and puts the default back when the page goes away. */
export function useDocumentTitle(page?: string | null): void {
  useEffect(() => {
    if (page === undefined) return
    document.title = formatTitle(page)
    return () => { document.title = APP_NAME }
  }, [page])
}

/**
 * Route-level fallback for pages that do not name themselves: applies only while the title is still the
 * default, so a page that calls useDocumentTitle (its effect runs first) always wins.
 */
export function useFallbackTitle(page?: string | null): void {
  useEffect(() => {
    if (!page || document.title !== APP_NAME) return
    document.title = formatTitle(page)
    return () => { document.title = APP_NAME }
  }, [page])
}
