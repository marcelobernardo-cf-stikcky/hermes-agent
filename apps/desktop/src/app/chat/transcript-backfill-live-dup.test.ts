import { expect, it } from 'vitest'

import { graftRefreshedTailOntoBackfill } from '@/app/chat/transcript-backfill'
import { preserveLocalPendingTurnMessages } from '@/app/session/hooks/use-session-actions/utils'
import type { ChatMessage } from '@/lib/chat-messages'

import cases from './fixtures/live-dup-cases.json'

// Anonymized renderer snapshots of turns paused on a clarify: the durable
// page (`stored`) already holds the turn's row while the window (`live`)
// still carries its streamed bubbles. Every refresh path grafts the page onto
// the window, then re-attaches the live turn. A tool call painted twice is
// the duplicate clarify card users saw.
const toolIds = (list: ChatMessage[]) =>
  list.flatMap(message => message.parts.flatMap(part => (part.type === 'tool-call' ? [part.toolCallId] : [])))

for (const { name, stored, live } of cases as unknown as { name: string; stored: ChatMessage[]; live: ChatMessage[] }[]) {
  it(`refresh paints each tool call once (${name})`, () => {
    const merged = preserveLocalPendingTurnMessages(graftRefreshedTailOntoBackfill(stored, live), live)
    const ids = toolIds(merged)

    expect(ids.filter((id, index) => ids.indexOf(id) !== index)).toEqual([])
    // Nothing the page committed is lost either.
    expect(ids).toEqual(expect.arrayContaining(toolIds(stored)))
  })
}
