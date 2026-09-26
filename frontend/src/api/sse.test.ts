import { describe, expect, it } from "vitest"
import { createSSEParser, type SSEMessage } from "./sse"

function parse(chunks: string[], flush = true): SSEMessage[] {
  const out: SSEMessage[] = []
  const p = createSSEParser((m) => out.push(m))
  chunks.forEach((c) => p.push(c))
  if (flush) p.flush()
  return out
}

describe("createSSEParser", () => {
  it("parses several events in one chunk", () => {
    const body = 'event: meta\ndata: {"a":1}\n\nevent: token\ndata: {"text":"hi"}\n\n'
    expect(parse([body])).toEqual([
      { event: "meta", data: '{"a":1}' },
      { event: "token", data: '{"text":"hi"}' },
    ])
  })

  it("reassembles events split at any byte", () => {
    const body = 'event: token\ndata: {"text":"₹15,000"}\n\nevent: done\ndata: {"latency_ms":5,"first_token_ms":2}\n\n'
    for (let i = 1; i < body.length; i++) {
      expect(parse([body.slice(0, i), body.slice(i)])).toEqual([
        { event: "token", data: '{"text":"₹15,000"}' },
        { event: "done", data: '{"latency_ms":5,"first_token_ms":2}' },
      ])
    }
  })

  it("handles CRLF, including a CRLF split across chunks", () => {
    expect(parse(["event: error\r", "\ndata: {\"message\":\"x\"}\r\n\r\n"])).toEqual([
      { event: "error", data: '{"message":"x"}' },
    ])
  })

  it("joins multi-line data, skips comments, defaults the event name", () => {
    expect(parse([": keepalive\n\ndata: a\ndata: b\n\n"])).toEqual([{ event: "message", data: "a\nb" }])
  })

  it("dispatches a final event without its blank line on flush only", () => {
    expect(parse(["event: done\ndata: {}"], false)).toEqual([])
    expect(parse(["event: done\ndata: {}"])).toEqual([{ event: "done", data: "{}" }])
  })

  it("ignores events without data", () => {
    expect(parse(["event: meta\n\n"])).toEqual([])
  })
})
