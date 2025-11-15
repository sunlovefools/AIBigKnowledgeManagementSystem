def split_into_chunks(text: str, max_chars: int = 3000):
    """Split a huge text into smaller chunks for easier processing.
    Args: 
        text (str): The input text to be split into chunks. (A huge string)
        max_chars (int): The maximum number of characters allowed in each chunk. (By default, 3000)
    Returns:
        List[Dict[str, Any]]: A list of dictionaries, each containing an "index" and "text" key.
        {index: int, text: str}:
            A list of chunks with their respective indices.
    """

    # Split the text into paragraphs based on double newlines
    # And remove empty paragraphs
    # It will result in a list of smaller strings (paragraphs)
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]

    # Initialize variables
    # Chunk list that store the final output
    # Current buffer: a temporary string to build the current chunk
    chunkList, currentBuffer = [], ""

    # A helper function to flush the current buffer into chunk lists
    def flush():
        # nonlocal to modify outer scope variable
        nonlocal currentBuffer # Access the outer scope variable, accessing currentBuffer
        # If currentBuffer is not empty, then append it to chunkList and reset currentBuffer
        if currentBuffer:
            chunkList.append(currentBuffer.strip())
            currentBuffer = ""

    # Process each paragraph in the huge paragraphs list
    for paragraph in paragraphs:
        # If paragraph fits within max_chars
        if len(paragraph) <= max_chars:
            # Here code trying to pack additional paragraphs into the current buffer
            # If adding the new paragraph to currentBuffer does not exceed max_chars, add it
            if len(currentBuffer) + len(paragraph) + 2 <= max_chars:
                currentBuffer = (currentBuffer + "\n\n" + paragraph) if currentBuffer else paragraph 
            else:
                # Otherwise (The currentBuffer is full), flush it and start a new buffer with the current paragraph
                flush()
                currentBuffer = paragraph
        # Else if paragraph is too long
        else:
            # if paragraph is way too long - cutting into sentences
            flush()
            # Split paragraph into sentences based on punctuation marks followed by space
            sentencesLists = paragraph.replace("! ", "!|").replace("? ", "?|").replace(". ", ".|").split("|")
            currentSentenceBuffer = ""
            # Loop through each sentence in the sentences list
            for sentence in sentencesLists:
                sentence = sentence.strip()
                # Skip empty sentences
                if not sentence:
                    continue
                # Ensure sentence ends with proper punctuation
                sentenceToAdd = (sentence if sentence.endswith((".", "!", "?")) else sentence + ".")

                # If adding the sentence to currentSentenceBuffer does not exceed max_chars, add it
                if len(currentSentenceBuffer) + len(sentenceToAdd) + 1 <= max_chars:
                    currentSentenceBuffer = (currentSentenceBuffer + " " + sentenceToAdd).strip() if currentSentenceBuffer else sentenceToAdd
                # Else (currentSentenceBuffer full), flush it and start a new buffer with the current sentence
                else:
                    # If currentSentenceBuffer is not empty and too long, append it to chunkList
                    if currentSentenceBuffer:
                        chunkList.append(currentSentenceBuffer.strip())
                    currentSentenceBuffer = sentenceToAdd
            # If there is any remaining sentence in currentSentenceBuffer, append it to chunkList
            if currentSentenceBuffer:
                chunkList.append(currentSentenceBuffer.strip())
    # Finally, flush any remaining text in currentBuffer
    flush()
    # back to index
    return [{"index": index, "text": chunk} for index, chunk in enumerate(chunkList)]
