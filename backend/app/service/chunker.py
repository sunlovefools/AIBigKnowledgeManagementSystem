def split_into_chunks(text: str, max_chars: int = 4000):
    # 1) Here code roughly cut along double hyphens (paragraphs)
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, cur = [], ""

    def flush():
        nonlocal cur
        if cur:
            chunks.append(cur.strip())
            cur = ""

    for p in paras:
        if len(p) <= max_chars:
            # Here code trying to pack additional paragraphs into the current buffer.
            if len(cur) + len(p) + 2 <= max_chars:
                cur = (cur + "\n\n" + p) if cur else p
            else:
                flush()
                cur = p
        else:
            # if paragraph is way too long - cutting into sentences
            flush()
            sent_parts = p.replace("! ", "!|").replace("? ", "?|").replace(". ", ".|").split("|")
            buf = ""
            for s in sent_parts:
                s = s.strip()
                if not s:
                    continue
                add = (s if s.endswith((".", "!", "?")) else s + ".")
                if len(buf) + len(add) + 1 <= max_chars:
                    buf = (buf + " " + add).strip() if buf else add
                else:
                    if buf:
                        chunks.append(buf.strip())
                    buf = add
            if buf:
                chunks.append(buf.strip())

    flush()
    # back to index
    return [{"index": i, "text": c} for i, c in enumerate(chunks)]
