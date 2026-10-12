"""Stable selectors for multiple paths heading in the same direction."""
from collections import Counter


def exit_options(connections):
    exits = []
    seen = set()
    for connection in connections:
        if not isinstance(connection, dict):
            continue
        key = (str(connection.get('direction', '?')).strip().lower(), connection.get('target_location_id'))
        if key in seen:
            continue
        seen.add(key)
        exits.append((key[0], connection))
    compass = {name: index for index, name in enumerate((
        "north", "northeast", "east", "southeast", "south", "southwest",
        "west", "northwest", "up", "down", "in", "out",
    ))}
    # Stable sorting groups branches without changing their relative numbering.
    exits.sort(key=lambda entry: (compass.get(entry[0], len(compass)), entry[0]))
    counts = Counter(direction for direction, _ in exits)
    indexes = Counter()
    options = []
    for direction, connection in exits:
        indexes[direction] += 1
        selector = f'{direction} {indexes[direction]}' if counts[direction] > 1 else direction
        options.append((selector, connection))
    return options
