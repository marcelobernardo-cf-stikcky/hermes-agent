import { expect, it } from 'vitest'

import { graftRefreshedTailOntoBackfill } from '@/app/chat/transcript-backfill'
import { preserveLocalPendingTurnMessages } from '@/app/session/hooks/use-session-actions/utils'
import { type ChatMessage, preserveLocalAssistantErrors } from '@/lib/chat-messages'

// A settled live bubble carries its turn's FINAL row id (message.complete
// stamps rowId = final_assistant_row_id). History folds the same turn into one
// row keyed by its FIRST row id, with the final text part pointing back at the
// final row (sourceRowId). Same turn, two different `rowId`s: any refresh that
// merges by `rowId` alone kept both and painted the reply twice, out of order
// (real renderer snapshot 20260930_091202_3802bc, 12:23 on the packaged build).

let seq = 0

interface Turn {
  user: ChatMessage
  fold: ChatMessage
  live: ChatMessage
}

function turn(firstRowId: number, toolCount: number): Turn {
  seq += 1
  const userRowId = firstRowId
  const foldRowId = firstRowId + 1
  const finalRowId = firstRowId + 2 + toolCount

  const tools = Array.from({ length: toolCount }, (_, index) => ({
    type: 'tool-call' as const,
    toolCallId: `call_${seq}_${index}`,
    toolName: 'terminal',
    args: {},
    result: 'ok'
  }))

  const reply = `reply ${seq}`

  return {
    user: { id: `${userRowId}-user`, role: 'user', rowId: userRowId, parts: [{ type: 'text', text: `prompt ${seq}` }] },
    fold: {
      id: `${foldRowId}-assistant`,
      role: 'assistant',
      rowId: foldRowId,
      parts: [...tools, { type: 'text', text: reply, sourceRowId: finalRowId }]
    },
    live: {
      id: `assistant-stream-${seq}`,
      role: 'assistant',
      rowId: finalRowId,
      pending: false,
      durableComplete: seq % 2 === 0,
      persistedTurn: {
        row_ids: [userRowId, foldRowId, finalRowId],
        complete: seq % 2 === 0,
        user_row_id: userRowId,
        final_assistant_row_id: finalRowId
      },
      // The live stream text often differs by leading whitespace only.
      parts: [...tools, { type: 'text', text: `\n\n${reply}`, sourceRowId: finalRowId }]
    }
  }
}

const refresh = (page: ChatMessage[], window: ChatMessage[]) =>
  preserveLocalAssistantErrors(
    preserveLocalPendingTurnMessages(graftRefreshedTailOntoBackfill(page, window), window),
    window
  )

const replies = (list: ChatMessage[]) =>
  list.flatMap(message =>
    message.role === 'assistant' ? message.parts.flatMap(part => (part.type === 'text' ? [part.text.trim()] : [])) : []
  )

it('a settled live bubble keyed by its final row is its folded row, not a second reply', () => {
  const turns = [turn(100, 2), turn(200, 3), turn(300, 15)]
  const page = turns.flatMap(t => [t.user, t.fold])
  // The window no longer holds the first turn's user row (it scrolled/windowed
  // out), so the page's first durable row is not on screen and the graft
  // falls through to the stored-id merge.
  const window = [turns[0].fold, turns[1].user, turns[1].fold, turns[2].user, turns[2].live]

  expect(replies(refresh(page, window))).toEqual(['reply 1', 'reply 2', `reply ${seq}`])
})

// Stress: random session shapes, random page start, random window start.
// Every refresh must paint each reply once and keep turn order.
it('stress: every refresh paints each turn once, in order', () => {
  let rng = 7

  const rand = (n: number) => {
    rng = (rng * 1103515245 + 12345) % 2 ** 31

    return rng % n
  }

  for (let iteration = 0; iteration < 5000; iteration += 1) {
    const count = 2 + rand(6)
    const turns: Turn[] = []
    let rowId = 10

    for (let index = 0; index < count; index += 1) {
      const t = turn(rowId, rand(5))
      turns.push(t)
      rowId = (t.live.rowId as number) + 1 + rand(3)
    }

    const pageStart = rand(count)
    const windowStart = rand(count)
    const liveTail = 1 + rand(Math.min(2, count))
    const page = turns.slice(pageStart).flatMap(t => [t.user, t.fold])

    const window = turns.slice(windowStart).flatMap((t, index, list) => {
      const isLive = index >= list.length - liveTail

      return rand(2) && index === 0 ? [isLive ? t.live : t.fold] : [t.user, isLive ? t.live : t.fold]
    })

    const expected = turns.slice(Math.min(pageStart, windowStart)).map(t => chatReply(t))
    const got = replies(refresh(page, window))

    // Order and uniqueness: got must be a strictly increasing subsequence of
    // the session's replies with no repeats, and include every paged reply.
    const positions = got.map(text => expected.indexOf(text))

    expect(
      positions.every(position => position >= 0),
      JSON.stringify({ iteration, got })
    ).toBe(true)
    expect(
      positions.every((position, index) => index === 0 || position > positions[index - 1]),
      JSON.stringify({ iteration, got, pageStart, windowStart })
    ).toBe(true)

    for (const t of turns.slice(pageStart)) {
      expect(got).toContain(chatReply(t))
    }
  }
})

function chatReply(t: Turn): string {
  const part = t.fold.parts.at(-1)

  return part?.type === 'text' ? part.text : ''
}
