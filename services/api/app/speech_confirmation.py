"""A finite matcher for a bound readback, with narrow name pronunciation tolerance.

Only integer automaton states persist, never the utterance. Party, date, time and
non-name terms must match (the reservation label "table" is optional). Names can
differ in vowels or doubled consonants when their full phonetic code matches;
this is not an identity/lookup check.
"""
GROUPS = ['bfpv', 'cgjkqsxz', 'dt', 'l', 'mn', 'r']
SILENT = set('aeiouhyw')
CODES = {char: str(i) for i, group in enumerate(GROUPS) for char in group}


def name_tokens(name):
    if not name:
        return []
    tokens = [(set(name[0]), False), (SILENT, True)]
    previous = CODES.get(name[0])
    if previous is not None:
        tokens.insert(1, (set(GROUPS[int(previous)]), True))
    for char in name[1:]:
        if char in SILENT:
            if char in 'aeiou':
                previous = None
            continue
        code = CODES.get(char, char)
        if code != previous:
            chars = set(GROUPS[int(code)]) if code.isdigit() and char in CODES else set(char)
            tokens.extend([(chars, False), (chars, True), (SILENT, True)])
            previous = code
    return tokens


def match_segment(action, text, canonicalize):
    readback = action.readback.removeprefix('Please confirm: ')
    request = action.payload['request']
    name = request['name']
    before, marker, after = readback.partition(f' for {name} on ')
    if request['kind'] in ['reservation', 'appointment'] and marker:
        if request['kind'] == 'reservation':
            before = before.removeprefix('table ')
        tokens = [(set(char), False) for char in canonicalize(before + ' for ')]
        tokens += name_tokens(canonicalize(name))
        tokens += [(set(char), False) for char in canonicalize(' on ' + after)]
    else:
        tokens = [(set(char), False) for char in canonicalize(readback)]

    def closure(states):
        states = set(states)
        for state in sorted(states):
            while state < len(tokens) and tokens[state][1]:
                state += 1
                states.add(state)
        return states

    active = closure(action.payload.get('readback_states', []))
    for char in canonicalize(text):
        active = closure(active | {0})
        active = closure({state if tokens[state][1] else state + 1 for state in active if state < len(tokens) and char in tokens[state][0]})
        if len(tokens) in active:
            action.payload = {**action.payload, 'readback_states': [], 'readback_progress': len(tokens)}
            return True
    action.payload = {**action.payload, 'readback_states': sorted(active), 'readback_progress': max(active, default=0)}
    return False
