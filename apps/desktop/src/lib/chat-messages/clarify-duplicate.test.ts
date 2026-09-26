import { expect, it } from 'vitest'

import { preserveLocalPendingTurnMessages } from '@/app/session/hooks/use-session-actions/utils'
import { mergeInFlightMessages } from '@/lib/inflight-turn-journal'

import type { ChatMessage } from './types'

// Real shape (session 20260925_010022_0b8d66): the committed row carries
// reasoning + text + clarify; the live stream sealed the text and the clarify
// as two bubbles; the journal also holds a copy of the committed row under an
// id from an older page load. Every path must paint the reply once.
const user = (id: string): ChatMessage => ({ id, role: 'user', rowId: 1, parts: [{ type: 'text', text: 'nova tarefa' }] })

const clarify = { type: 'tool-call', toolCallId: 'toolu_1', toolName: 'clarify', args: {} } as ChatMessage['parts'][number]

const committed = (id: string): ChatMessage => ({
  id,
  role: 'assistant',
  rowId: 2,
  parts: [{ type: 'reasoning', text: 'pensando' }, { type: 'text', text: 'Antes de mexer' }, clarify]
})

const db = [user('u-11'), committed('a-12')]

const live: ChatMessage[] = [
  { id: 'assistant-stream-2', role: 'assistant', pending: false, interim: true, parts: [{ type: 'text', text: 'Antes de mexer' }] },
  { id: 'assistant-stream-3', role: 'assistant', pending: true, parts: [clarify] }
]

const replyCount = (list: ChatMessage[]) => list.filter(m => m.role === 'assistant').length

it('journal recovery does not re-append a committed reply', () => {
  const journal = [user('u-118'), committed('a-119'), ...live]

  expect(replyCount(mergeInFlightMessages(db, journal, { keepPending: true }).messages)).toBe(1)
  expect(replyCount(mergeInFlightMessages(db, [user('u-118'), ...live], { keepPending: true }).messages)).toBe(1)
})

it('refresh does not keep a pending clarify bubble the committed row already paints', () => {
  expect(replyCount(preserveLocalPendingTurnMessages(db, [user('u-118'), ...live]))).toBe(1)
})
