major_scale = [0, 2, 4, 5, 7, 9, 11]
minor_scale = [0, 2, 3, 5, 7, 8, 10]

major_notes = [[e + octave for e in major_scale]
               for octave in range(0, 127, 12)]
major_notes_flattened = [e for l in major_notes for e in major_notes]

minor_notes = [[e + octave for e in minor_scale]
               for octave in range(0, 127, 12)]
minor_notes_flattened = [e for l in minor_notes for e in minor_notes]

black_key_map = [1, 3, 6, 8, 10]
black_keys = [[e + octave for e in black_key_map]
              for octave in range(0, 127, 12)]
black_keys_flattened = [e for l in black_keys for e in l]

white_key_map = [0, 2, 4, 5, 7, 11]
white_keys = [[e + octave for e in white_key_map]
              for octave in range(0, 127, 12)]
white_keys_flattened = [e for l in white_keys for e in l]


class MidiState(object):
    """
    Represents the current state of the virtual keyboard.
    Remembers active notes for every channel at the current timestep.
    """

    def __init__(self):
        self.channels = [set() for _ in range(16)]

    def handle_message(self, msg):
        if msg.type == "note_on":
            self.channels[msg.channel].add(msg.note)
        elif msg.type == "note_off" and msg.note in self.channels[msg.channel]:
            self.channels[msg.channel].remove(msg.note)

    def active_notes(self, channel):
        return list(self.channels[channel])

    def reset(self):
        self.channels = [set() for _ in range(16)]
