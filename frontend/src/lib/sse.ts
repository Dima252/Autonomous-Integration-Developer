// Incremental Server-Sent Events parser. EventSource only does GET, and the chat
// endpoint is a POST, so its response body is parsed by hand.

export interface SseMessage {
  event: string;
  id?: string;
  data: string;
}

export class SseParser {
  private buffer = "";

  /** Feeds a chunk of the body; returns every event it completed. */
  push(chunk: string): SseMessage[] {
    this.buffer += chunk.replace(/\r\n?/g, "\n");
    const out: SseMessage[] = [];
    let end: number;
    while ((end = this.buffer.indexOf("\n\n")) !== -1) {
      const block = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      const message = parseBlock(block);
      if (message) out.push(message);
    }
    return out;
  }
}

function parseBlock(block: string): SseMessage | null {
  let event = "message";
  let id: string | undefined;
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue; // blank or keep-alive comment
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "id") id = value;
    else if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  return { event, id, data: data.join("\n") };
}
