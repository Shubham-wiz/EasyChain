/**
 * Read Server-Sent Events from a fetch() body, yielding each event's data. Leaving the
 * `for await` loop early (break, return, throw) cancels the body, which closes the connection.
 */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let ended = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) {
        ended = true;
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let sep: number;
      while ((sep = buffer.search(/\r?\n\r?\n/)) !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(buffer[sep] === "\r" ? sep + 4 : sep + 2);
        const data = block
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).replace(/^ /, ""))
          .join("\n");
        if (data) yield data;
      }
    }
    const rest = buffer.trim();
    if (rest.startsWith("data:")) yield rest.slice(5).trim();
  } finally {
    if (!ended) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
