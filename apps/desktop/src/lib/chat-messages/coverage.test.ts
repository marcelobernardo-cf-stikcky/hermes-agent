import { expect, it } from 'vitest'

import { withoutCoveredAssistantPrefix } from './coverage'
import type { ChatMessage } from './types'

const msg = (id: string, parts: ChatMessage['parts']): ChatMessage => ({ id, role: 'assistant', parts })

// Live stream sealed "text" then "clarify" as two bubbles without the reasoning
// the durable row carries; the row still covers both (real duplicate case).
it('treats durable reasoning absent from live bubbles as covered', () => {
  const stored = [
    msg('row', [
      { type: 'reasoning', text: 'thinking' },
      { type: 'text', text: 'Antes de mexer' },
      { type: 'tool-call', toolCallId: 'toolu_1', toolName: 'clarify', args: {} }
    ] as ChatMessage['parts'])
  ]

  const live = [
    msg('assistant-stream-1', [{ type: 'text', text: 'Antes de mexer' }] as ChatMessage['parts']),
    msg('assistant-stream-2', [
      { type: 'tool-call', toolCallId: 'toolu_1', toolName: 'clarify', args: {} }
    ] as ChatMessage['parts'])
  ]

  expect(withoutCoveredAssistantPrefix(stored, live)).toEqual([])
})

it('keeps live output when only text matches (no tool anchor)', () => {
  const stored = [msg('row', [{ type: 'reasoning', text: 'x' }, { type: 'text', text: 'same' }] as ChatMessage['parts'])]
  const live = [msg('assistant-stream-1', [{ type: 'text', text: 'same' }] as ChatMessage['parts'])]

  expect(withoutCoveredAssistantPrefix(stored, live)).toBe(live)
})
