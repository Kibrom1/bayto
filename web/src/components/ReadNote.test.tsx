import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ReadNote, describeRead, readEntries } from './ReadNote'

const META = {
  attachments: [
    { name: 'contract.txt', mode: 'excerpt', parts: [3, 7], total: 41 },
    { name: 'notes.md', mode: 'whole', parts: [], total: 2 },
  ],
}

describe('ReadNote', () => {
  it('says which parts of which attachment were read, collapsed to one line', () => {
    render(<ReadNote meta={META} />)
    expect(screen.getByText('Read: contract.txt parts 3, 7 of 41 · notes.md (whole)')).toBeInTheDocument()
    expect(document.querySelector('details')).not.toHaveAttribute('open')
  })

  it('renders nothing when nothing was attached', () => {
    const { container } = render(<ReadNote meta={{ tokens_in: 3 }} />)
    expect(container).toBeEmptyDOMElement()
    expect(readEntries(undefined)).toEqual([])
    expect(readEntries({ attachments: 'x' })).toEqual([])
  })

  it('describes each way an attachment can have been shown', () => {
    expect(describeRead({ name: 'a', mode: 'excerpt', parts: [2], total: 9 })).toBe('a part 2 of 9')
    expect(describeRead({ name: 'a', mode: 'start', parts: [], total: 9 })).toBe('a (start only, of 9 passages)')
    expect(describeRead({ name: 'a', mode: 'not_shown', parts: [], total: 1 })).toBe('a (not shown, 1 passage)')
  })
})
