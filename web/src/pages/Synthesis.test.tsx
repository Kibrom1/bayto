import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { ArtifactOut } from '../api/sessions'
import { Synthesis, synthesisMarkdown } from './Synthesis'

afterEach(cleanup)
const arts = [{ id: 's', type: 'synthesis', content_json: { decision: 'Ship it' }, source_turn_ids: [] }] as ArtifactOut[]

describe('Synthesis', () => {
  it('has no export buttons of its own; the room has one Export menu', () => {
    render(<Synthesis title="T" artifacts={arts} />)
    expect(screen.getByText('Ship it')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })
  it('builds Markdown for the Export menu, or null without a synthesis', () => {
    expect(synthesisMarkdown('T', arts)).toContain('# T')
    expect(synthesisMarkdown('T', arts)).toContain('Ship it')
    expect(synthesisMarkdown('T', [])).toBeNull()
  })
})
