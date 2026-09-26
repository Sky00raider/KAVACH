// Incremental parser for text/event-stream bodies read with fetch + ReadableStream (CONTRACT §10).

export interface SSEMessage {
  event: string
  data: string
}

const EOL = /\r\n|\r|\n/

export function createSSEParser(onMessage: (msg: SSEMessage) => void) {
  let buffer = ""
  let event = ""
  let data: string[] = []

  function dispatch() {
    if (data.length > 0) onMessage({ event: event || "message", data: data.join("\n") })
    event = ""
    data = []
  }

  function line(raw: string) {
    if (raw === "") return dispatch()
    if (raw.startsWith(":")) return
    const colon = raw.indexOf(":")
    const field = colon === -1 ? raw : raw.slice(0, colon)
    let value = colon === -1 ? "" : raw.slice(colon + 1)
    if (value.startsWith(" ")) value = value.slice(1)
    if (field === "event") event = value
    else if (field === "data") data.push(value)
  }

  return {
    push(chunk: string) {
      buffer += chunk
      // Hold back a trailing "\r": it may be the first half of a "\r\n" split across chunks.
      const hold = buffer.endsWith("\r") ? 1 : 0
      const lines = buffer.slice(0, buffer.length - hold).split(EOL)
      const rest = lines.pop() ?? ""
      lines.forEach(line)
      buffer = rest + (hold ? "\r" : "")
    },
    /** End of stream: a final event without its blank line still counts. */
    flush() {
      if (buffer) buffer.split(EOL).forEach(line)
      buffer = ""
      dispatch()
    },
  }
}
