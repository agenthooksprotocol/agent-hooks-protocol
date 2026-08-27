# stdio binding profile

**Status: Working Draft — `draft`.**

Each protocol message is one complete UTF-8 JSON-RPC object followed by one newline. Standard input carries messages to the backend. Standard output carries only protocol messages; diagnostics use standard error. A persistent process can exchange multiple lines. A process-per-event backend receives one request or notification and exits after any required response.

A framing check rejects pretty-printed multi-line objects, banners, log lines, and literal unescaped newlines in JSON text on standard output.
