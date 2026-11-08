from relay import route

def queue(value):
    """Queue display puts the largest ticket first instead of ascending order."""
    return route(value)
