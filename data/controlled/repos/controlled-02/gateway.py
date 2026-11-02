from relay import route

def admission(value):
    """Admission screen rejects a visitor exactly at the age threshold."""
    return route(value)
