import { act, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { type MessageStreamHarness, renderMessageStream } from './test-harness'
import { STREAM_DELTA_FLUSH_MS } from './utils'

const SID = 'turn-identity-session'

let stream: MessageStreamHarness

/**
 * Cross-turn contamination guard (turn_id).
 *
 * The `completedTurn` guard from e54e7bf3e1 only drops late payload while the
 * turn stays terminal. A `message.start` for the NEXT turn resets
 * `sawAssistantPayload` and re-arms `turnLive`/`busy`, so a delta belonging to
 * the PREVIOUS turn that lands after that start walks straight past the guard
 * and contaminates the new turn's bubble.
 *
 * Ordering here is legitimate, not synthetic: the gateway serializes frames on
 * the happy path, but reconnect replay and run supersession both re-deliver an
 * older turn's payload after a newer turn opened.
 */
describe('cross-turn identity guard', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('drops a previous turn delta that arrives after the next turn started', async () => {
    vi.useFakeTimers()
    stream = renderMessageStream(SID)
    await act(async () => {
      await Promise.resolve()
    })

    // Turn 1 runs to completion.
    act(() =>
      stream.handleEvent({ payload: { turn_id: 't1' }, session_id: SID, type: 'message.start' })
    )
    act(() =>
      stream.handleEvent({
        payload: { text: 'answer one', turn_id: 't1' },
        session_id: SID,
        type: 'message.complete'
      })
    )

    // Turn 2 opens — this is what resets the completedTurn guard.
    act(() =>
      stream.handleEvent({ payload: { turn_id: 't2' }, session_id: SID, type: 'message.start' })
    )

    // Late delta from turn 1, delivered inside turn 2's live window.
    act(() =>
      stream.handleEvent({
        payload: { text: 'STALE FROM TURN ONE', turn_id: 't1' },
        session_id: SID,
        type: 'message.delta'
      })
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(STREAM_DELTA_FLUSH_MS)
    })

    const contaminated = stream
      .state()
      .messages.filter(message =>
        message.parts.some(part => part.type === 'text' && part.text.includes('STALE FROM TURN ONE'))
      )

    expect(contaminated).toHaveLength(0)
  })

  it('still applies a delta whose turn_id matches the live turn', async () => {
    vi.useFakeTimers()
    stream = renderMessageStream(SID)
    await act(async () => {
      await Promise.resolve()
    })

    act(() =>
      stream.handleEvent({ payload: { turn_id: 't1' }, session_id: SID, type: 'message.start' })
    )
    act(() =>
      stream.handleEvent({
        payload: { text: 'legit text', turn_id: 't1' },
        session_id: SID,
        type: 'message.delta'
      })
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(STREAM_DELTA_FLUSH_MS)
    })

    expect(stream.state().messages.at(-1)?.parts).toMatchObject([
      { type: 'text', text: 'legit text' }
    ])
  })

  it('accepts a delta with no turn_id (legacy emitter compatibility)', async () => {
    // Owner constraint: an event without the identifier keeps working, so an
    // older gateway paired with a newer renderer does not lose content.
    vi.useFakeTimers()
    stream = renderMessageStream(SID)
    await act(async () => {
      await Promise.resolve()
    })

    act(() => stream.handleEvent({ payload: {}, session_id: SID, type: 'message.start' }))
    act(() =>
      stream.handleEvent({
        payload: { text: 'legacy payload' },
        session_id: SID,
        type: 'message.delta'
      })
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(STREAM_DELTA_FLUSH_MS)
    })

    expect(stream.state().messages.at(-1)?.parts).toMatchObject([
      { type: 'text', text: 'legacy payload' }
    ])
  })

  it('accepts a turn_id delta when the live turn was started by a legacy start', async () => {
    // Mixed-version window: old gateway opened the turn (no turn_id), a newer
    // emitter stamps deltas. Identity is unknown, not mismatched — keep it.
    vi.useFakeTimers()
    stream = renderMessageStream(SID)
    await act(async () => {
      await Promise.resolve()
    })

    act(() => stream.handleEvent({ payload: {}, session_id: SID, type: 'message.start' }))
    act(() =>
      stream.handleEvent({
        payload: { text: 'mixed version', turn_id: 't9' },
        session_id: SID,
        type: 'message.delta'
      })
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(STREAM_DELTA_FLUSH_MS)
    })

    expect(stream.state().messages.at(-1)?.parts).toMatchObject([
      { type: 'text', text: 'mixed version' }
    ])
  })
})
