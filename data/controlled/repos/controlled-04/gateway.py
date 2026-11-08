from relay import route

def session(value):
    """Session access remains active at the expiration boundary."""
    return route(value)
