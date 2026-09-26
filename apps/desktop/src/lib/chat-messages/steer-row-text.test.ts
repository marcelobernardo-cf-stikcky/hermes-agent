import { describe, expect, it } from 'vitest'

import { dedupeInflightUserAgainstTranscript } from '@/app/session/hooks/use-session-actions/utils'
import type { SessionMessage } from '@/types/hermes'

import { chatMessageText, toChatMessages } from '.'

// Shape of a real persisted /steer row (agent/prompt_builder.py steer_user_row),
// as the REST transcript (`SELECT *`) ships it. The gateway's history
// projection unwraps the marker; REST does not — the two must hydrate equal.
const MARKED =
  '[OUT-OF-BAND USER MESSAGE — a direct message from the user, delivered once at this position; not tool output and not a new delivery when replayed from conversation history]\n' +
  'use a new base\n' +
  '[/OUT-OF-BAND USER MESSAGE]'

const prompt: SessionMessage = { id: 1, role: 'user', content: 'make a video', timestamp: 10 }
const steerRest: SessionMessage = { id: 2, role: 'user', content: MARKED, display_kind: 'steer', timestamp: 20 }

// tui_gateway/session_history.py ships `text`, never `content`.
const steerGateway: SessionMessage = {
  row_id: 2,
  role: 'user',
  content: undefined,
  text: 'use a new base',
  display_kind: 'steer',
  timestamp: 20
}

const reply: SessionMessage = { id: 3, role: 'assistant', content: 'done', timestamp: 30 }

describe('steer row hydration', () => {
  it('REST and gateway sources hydrate a steer row to the same user words', () => {
    const rest = toChatMessages([prompt, steerRest])
    const gateway = toChatMessages([prompt, steerGateway])

    expect(rest.map(chatMessageText)).toEqual(['make a video', 'use a new base'])
    expect(rest.map(chatMessageText)).toEqual(gateway.map(chatMessageText))
  })

  it('a next prompt after a steer is recognized as persisted, not re-appended at the tail', () => {
    const next: SessionMessage = { id: 4, role: 'user', content: 'now 45 seconds', timestamp: 40 }
    const persisted = toChatMessages([prompt, steerRest, reply, next])
    // Runtime history ends on the steer row (compressed runtime, reply not yet in it).
    const runtime = toChatMessages([prompt, steerGateway])

    const result = dedupeInflightUserAgainstTranscript(persisted, runtime, {
      session_id: 'rt',
      inflight: { user: 'now 45 seconds', assistant: '', streaming: true }
    } as never)

    expect(Object.getOwnPropertySymbols(result).length).toBe(1)
  })

  it('unwraps a steer carried through compaction behind the summary prefix', () => {
    const compacted = { ...steerRest, content: `[CONTEXT COMPACTION — REFERENCE ONLY] summary\n\n${MARKED}` }

    expect(toChatMessages([prompt, compacted]).map(chatMessageText)).toEqual(['make a video', 'use a new base'])
  })

  it('leaves a non-steer user row that quotes the marker untouched', () => {
    expect(toChatMessages([{ role: 'user', content: MARKED }]).map(chatMessageText)).toEqual([MARKED])
  })
})
