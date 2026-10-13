"""A finite matcher for a bound readback, with narrow name pronunciation tolerance.

Only integer automaton states persist, never the utterance. Party, date, time and
non-name terms must match (the reservation label "table" is optional). Names can
differ in vowels or doubled consonants when their full phonetic code matches;
this is not an identity/lookup check.

How it works: the authoritative readback is compiled into a list of tokens, each a
set of acceptable characters plus an 'optional' flag. The caller's speech is fed
character by character through a small non-deterministic automaton (a set of active
positions) that may begin matching at any point, so extra words before or after
are tolerated. The progress is stored on the action payload as integer states.
"""
GROUPS = ['bfpv', 'cgjkqsxz', 'dt', 'l', 'mn', 'r']
SILENT = set('aeiouhyw')
CODES = {char: str(i) for i, group in enumerate(GROUPS) for char in group}


def name_tokens(name):
    """Compile a canonicalized name into automaton tokens with a Soundex-like phonetic tolerance.

    Consonants in the same GROUPS are interchangeable, vowels/h/y/w are optional, and repeated codes
    collapse, so 'Jon'/'John' match but a different consonant skeleton does not.
    """
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
    """Advance the readback matcher with new speech; True once the whole readback has matched.

    Args: action (reads readback and payload['request']; writes payload['readback_states'] and
    'readback_progress'), text (latest utterance chunk), canonicalize (normalizer for spoken text/numbers).
    Only the numeric automaton state is persisted, never the utterance. Date, time, party size and other
    terms must match exactly; only the name segment is phonetically tolerant.
    """
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
        """Expand states across optional tokens (epsilon moves)."""
        states = set(states)
        for state in sorted(states):
            while state < len(tokens) and tokens[state][1]:
                state += 1
                states.add(state)
        return states

    # Resume from persisted states so a readback can be matched across several utterances.
    active = closure(action.payload.get('readback_states', []))
    for char in canonicalize(text):
        # Matching may restart at the beginning at any character (caller can say filler first).
        active = closure(active | {0})
        active = closure({state if tokens[state][1] else state + 1 for state in active if state < len(tokens) and char in tokens[state][0]})
        if len(tokens) in active:
            action.payload = {**action.payload, 'readback_states': [], 'readback_progress': len(tokens)}
            return True
    action.payload = {**action.payload, 'readback_states': sorted(active), 'readback_progress': max(active, default=0)}
    return False
