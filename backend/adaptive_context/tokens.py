def count_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars per token). Swap for tiktoken if exact counts matter."""
    return (len(text) + 3) // 4
