### System ###
from time import sleep
from threading import Thread, Event
from sortedcontainers import SortedSet

### Mido ###
from mido import open_input, open_output, get_input_names, get_output_names  # pylint: disable-msg=no-name-in-module

### Local ###
from .midi_meta import MidiState, major_notes


class MidiHarmonizer(Thread):
    """
    Relays MIDI messages by proxying a MIDI connection between virtual or hardware ports.
    Applies a harmonization algorithm to MIDI messages on specific channels.
    """

    def __init__(self, port_in_name, port_out_name, melody_channel=1, bass_channel=2, chord_channel=3, callback=None):
        super(MidiHarmonizer, self).__init__()
        self.port_in_name = port_in_name
        self.port_in = None
        self.port_out_name = port_out_name
        self.port_out = None
        self.melody_channel = melody_channel
        self.bass_channel = bass_channel
        self.chord_channel = chord_channel
        self.callback = callback
        self.midi_state = MidiState()
        self._stop_event = Event()

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

    def shutdown(self):
        self.port_in.close()
        self.port_out.close()

    def run(self):
        if self.port_in_name in get_input_names():
            self.port_in = open_input(self.port_in_name, virtual=False)
        else:
            self.port_in = open_input(self.port_in_name, virtual=True)

        if self.port_out_name in get_output_names():
            self.port_out = open_output(self.port_out_name, virtual=False)
        else:
            self.port_out = open_output(self.port_out_name, virtual=True)

        # self.port_out.send(Message(type='program_change', program=57, channel=self.melody_channel))
        # self.port_out.send(Message(type='program_change', program=35, channel=self.melody_channel))
        # self.port_out.send(Message(type='program_change', program=35, channel=self.chord_channel))

        # Set the callback and go live
        self.port_in.callback = self.handle_message

        # Enter keep-alive main loop
        while True:
            if self.stopped():
                self.shutdown()
                break
            sleep(1)

    def fit_note(self, note):
        # TODO: possibly add scale notes to valid notes
        chord = self.midi_state.active_notes(self.chord_channel)
        # TODO: this currently maps to black AND white keys, MelodicFlow maps only to white keys.
        # This extends the range on the keyboard, but this solution should be more easily compatible
        # with generated output, as we don't have to transpose the black keys.
        # TODO: do not recompute if same chord as before (cache valid notes)
        if chord:
            # for bass, transpose up to melody register, then transpose final note down again
            note = note + 36

            middle_octave_chords = 4
            middle_octave_melody = 8

            # Root C note of all octaves
            octaves = list(range(0, 127, 12))

            # normalize chord to C0, then generate tranposed chords for every octave
            lowest, count = min(chord), -1
            while lowest >= 0:
                count += 1
                lowest -= 12
            mapped_over_range = [
                [e - (12 * count) + octave for e in chord] for octave in octaves]

            # get valid notes, split for positive and negative movement
            f_a = SortedSet([e for l in mapped_over_range[:middle_octave_chords] for e in l])
            f_a.update([e for l in major_notes[:middle_octave_chords] for e in l])
            f_b = SortedSet([e for l in mapped_over_range[middle_octave_chords:] for e in l])
            f_b.update([e for l in major_notes[middle_octave_chords:] for e in l])

            # get relative distance from played key to middle C of melody
            diff = note - octaves[middle_octave_melody]

            # clamp to valid note range
            diff = max(-len(f_a), min(diff, len(f_b) - 1))

            # jump to next valid note, either up or down
            if diff < 0:
                note = f_a[len(f_a) + diff]
            else:
                note = f_b[diff]

            # note = note - 36 # for bass
            # clamp note to valid MIDI note range
            note = max(0, min(note, 127))

        return note

    def handle_message(self, msg):
        # Update MidiState
        self.midi_state.handle_message(msg)

        new_msg = msg.copy()

        # Modify only note messages on the melody channel
        if msg.type and msg.type in ["note_on", "note_off"] and msg.channel in [self.melody_channel, self.bass_channel]:
            new_msg.note = self.fit_note(msg.note)

        # Shift bass notes to correct pitch
        if new_msg.channel == 2:
            new_msg.note -= 12

        # Relay all messages
        if self.callback:
            self.callback(msg, new_msg)

        self.port_out.send(new_msg)
