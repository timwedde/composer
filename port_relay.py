import sys
import logging
from enum import Enum
from decimal import *
from time import sleep, time
from signal import signal, SIGINT
from threading import Thread, Event
from sortedcontainers import SortedSet
from mido.midifiles.tracks import merge_tracks, _to_reltime
from mido.midifiles.units import second2tick, bpm2tempo
from mido import open_input, open_output, get_input_names, get_output_names, Message, MidiFile, MidiTrack

major_scale = [0, 2, 4, 5, 7, 9, 11]
minor_scale = [0, 2, 3, 5, 7, 8, 10]

major_notes = [[e + octave for e in major_scale]
               for octave in range(0, 127, 12)]
# major_notes = [e for l in major_notes for e in major_notes][:75]

minor_notes = [[e + octave for e in minor_scale]
               for octave in range(0, 127, 12)]
# minor_notes = [e for l in minor_notes for e in minor_notes][:75]

black_key_map = [1, 3, 6, 8, 10]
black_keys = [[e + octave for e in black_key_map]
              for octave in range(0, 127, 12)]
black_keys = [e for l in black_keys for e in l]

white_key_map = [0, 2, 4, 5, 7, 11]
white_keys = [[e + octave for e in white_key_map]
              for octave in range(0, 127, 12)]

delimiter_map = sorted([e[2] for e in white_keys] + [e[5] for e in white_keys])

white_keys = [e for l in white_keys for e in l]


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


class MidiHarmonizer(Thread):
    """
    Relays MIDI messages by proxying a MIDI connection between virtual or hardware ports.
    Applies a harmonization algorithm to MIDI messages on specific channels.
    """

    def __init__(self, port_in_name, port_out_name, melody_channel=1, chord_channel=2, callback=None):
        super(MidiHarmonizer, self).__init__()
        self.port_in_name = port_in_name
        self.port_out_name = port_out_name
        self.melody_channel = melody_channel
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
        # print("Chord is '{}'".format(chord))
        # TODO: this currently maps to black AND white keys, MelodicFlow maps only to white keys.
        # This extends the range on the keyboard, but this solution should be more easily compatible
        # with generated output, as we don't have to transpose the black keys.
        # TODO: do not recompute if same chord as before (cache valid notes)
        if chord:
            # for bass, transpose up to melody register, then transpose final
            # note down again
            note = note + 36

            middle_octave_chords = 4
            middle_octave_melody = 8

            # Root C note of all octaves
            octaves = list(range(0, 127, 12))

            # normalize chord to C0, then generate tranposed chords for every
            # octave
            lowest, count = min(chord), -1
            while lowest >= 0:
                count += 1
                lowest -= 12
            mapped_over_range = [
                [e - (12 * count) + octave for e in chord] for octave in octaves]

            # get valid notes, split for positive and negative movement
            f_a = SortedSet([e for l in mapped_over_range[
                            :middle_octave_chords] for e in l])
            f_a.update([e for l in major_notes[:middle_octave_chords]
                        for e in l])
            f_b = SortedSet([e for l in mapped_over_range[
                            middle_octave_chords:] for e in l])
            f_b.update([e for l in major_notes[middle_octave_chords:]
                        for e in l])

            # get relative distance from played key to middle C of melody
            diff = note - octaves[middle_octave_melody]
            # print("Diff to middle C: {}".format(diff))
            # clamp to valid note range
            diff = max(-len(f_a), min(diff, len(f_b) - 1))
            # print("Valid Range: {} - {}".format(octaves[middle_octave_chords]-len(f_a), octaves[middle_octave_chords]+len(f_b)))

            # jump to next valid note, either up or down
            original = note
            if diff < 0:
                note = f_a[len(f_a) + diff]
            else:
                note = f_b[diff]

            # note = note - 36 # for bass
            # clamp note to valid MIDI note range
            note = max(0, min(note, 127))

            # print("Note: {} => {}".format(original, note))

        return note

    def handle_message(self, msg):
        # Update MidiState
        self.midi_state.handle_message(msg)

        new_msg = msg.copy()

        # Modify only note messages on the melody channel
        if msg.type and (msg.type == "note_on" or msg.type == "note_off") and msg.channel == self.melody_channel:
            new_msg.note = self.fit_note(msg.note)
            # msg.note = self.fit_note(msg.note)

        if new_msg.channel == 9:
            new_msg.note -= 24

        # Relay all messages
        if self.callback:
            self.callback(msg, new_msg)

        self.port_out.send(new_msg)


class MidiRecorder(Thread):
    """
    Records incoming MIDI messages into a properly-formed MIDI file.
    Also functions as a relay.
    """

    def __init__(self, port_in_name, port_out_name, callback=None):
        super(MidiRecorder, self).__init__()
        self.port_in_name = port_in_name
        self.port_out_name = port_out_name
        self.callback = callback
        self.first_time = None
        self.tracks = [MidiTrack(), MidiTrack(), MidiTrack()]
        self._stop_event = Event()

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

    def shutdown(self):
        self.port_in.close()
        self.port_out.close()
        midi_file = MidiFile()
        for track in self.tracks:
            t = MidiTrack(_to_reltime(track))
            midi_file.tracks.append(t)
        midi_file.save("recording.mid")

    def run(self):
        if self.port_in_name in get_input_names():
            self.port_in = open_input(self.port_in_name, virtual=False)
        else:
            self.port_in = open_input(self.port_in_name, virtual=True)

        if self.port_out_name in get_output_names():
            self.port_out = open_output(self.port_out_name, virtual=False)
        else:
            self.port_out = open_output(self.port_out_name, virtual=True)

        # Set the callback and go live
        self.port_in.callback = self.handle_message

        # Enter keep-alive main loop
        while True:
            if self.stopped():
                self.shutdown()
                break
            sleep(1)

    def handle_message(self, msg):
        # truncate time value to 3-digit precision
        # this is done because the magenta-emitted time values
        # are distorted and precision is lost in transmission.
        # 3 digits is the most precise we can get
        with localcontext() as ctx:
            ctx.rounding = ROUND_DOWN
            tm = float(Decimal(time()).quantize(Decimal('0.001')))
            if not self.first_time:
                self.first_time = tm
                tm = 0
            else:
                tm -= self.first_time
            tk = int(second2tick(tm, 480, bpm2tempo(120)))
            msg.time = tk

        try:
            if msg.channel == 9:
                self.tracks[0].append(msg)
            else:
                self.tracks[msg.channel].append(msg)
        except:
            pass

        if self.callback:
            self.callback(msg)

        self.port_out.send(msg)


class KeyType(Enum):
    BLACK = 0
    WHITE = 1


class KeyState(Enum):
    INACTIVE = 0
    ACTIVE = 1


class Delimiter(object):

    def draw(self):
        return "|||||"


class Key(object):

    def __init__(self, num):
        super(Key, self).__init__()
        if not 0 <= num <= 127:
            raise IndexError("Key number must be in range 0 - 127")
        self.num = num
        self.type = KeyType.BLACK if num in black_keys else KeyType.WHITE
        self.state = KeyState.INACTIVE

    def active(self):
        return self.state == KeyState.ACTIVE

    def activate(self):
        self.state = KeyState.ACTIVE

    def deactivate(self):
        self.state = KeyState.INACTIVE

    def draw(self):
        if self.type == KeyType.WHITE:
            if self.state == KeyState.INACTIVE:
                return "    _"
            else:
                return "xxxxx"
        else:
            if self.state == KeyState.INACTIVE:
                return "###||"
            else:
                return "ooo||"

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.num == other.num
        elif isinstance(other, int):
            return self.num == other
        else:
            return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __pos__(self):
        return Key(self.num + 1)

    def __neg__(self):
        return Key(self.num - 1)

    def __add__(self, other):
        if isinstance(other, self.__class__):
            return Key(self.num + other.num)
        elif isinstance(other, int):
            return Key(self.num + other)
        else:
            raise TypeError("Can not add Key() and {}".format(other.__class__))

    def __sub__(self, other):
        if isinstance(other, self.__class__):
            return Key(abs(self.num - other.num))
        elif isinstance(other, int):
            return Key(abs(self.num - other))
        else:
            raise TypeError(
                "Can not subtract Key() and {}".format(other.__class__))

    def __lt__(self, other):
        if isinstance(other, self.__class__):
            return self.num < other.num
        elif isinstance(other, int):
            return self.num < other
        else:
            raise TypeError(
                "Can not subtract Key() and {}".format(other.__class__))

    def __gt__(self, other):
        if isinstance(other, self.__class__):
            return self.num > other.num
        elif isinstance(other, int):
            return self.num > other
        else:
            raise TypeError(
                "Can not subtract Key() and {}".format(other.__class__))

    def __le__(self, other):
        if isinstance(other, self.__class__):
            return self.num <= other.num
        elif isinstance(other, int):
            return self.num <= other
        else:
            raise TypeError(
                "Can not subtract Key() and {}".format(other.__class__))

    def __ge__(self, other):
        if isinstance(other, self.__class__):
            return self.num >= other.num
        elif isinstance(other, int):
            return self.num >= other
        else:
            raise TypeError(
                "Can not subtract Key() and {}".format(other.__class__))

    def __repr__(self):
        return "Key(Index {}, {}, {})".format(self.num, self.type, self.state)


class Keyboard(object):

    def __init__(self, octaves=4, channel=0, note_shift=0):
        if not 0 < octaves < 11:
            raise IndexError("Octave number must be in range 1 - 10")
        self.keys = []
        self.octaves = octaves
        for i in range(self.octaves * 12):
            self.keys.append(Key(i))
        self.midi_state = MidiState()
        self.channel = channel
        self.note_shift = -note_shift

    def keyboard_header(self, width):
        extra_width = width - 57
        return [" {} ".format("_" * (width - 2)),
                "|:::::: o o o o . |..... . .. . |{} [{:2d}]  o o o o o ::::::|".format(" " * extra_width, self.channel),
                "|:::::: o o o o   | ..  . ..... |{}       o o o o o ::::::|".format(" " * extra_width),
                "|::::::___________|__..._...__._|{}_________________::::::|".format("_" * extra_width)]

    def draw(self):
        chars = []
        size = 2
        active_notes = self.midi_state.active_notes(self.channel)
        chars.append(Delimiter().draw())
        for key in self.keys:
            if key > 1 and key - 1 in delimiter_map:
                size += 1
                chars.append(Delimiter().draw())
            size += 1
            if key + self.note_shift in active_notes:
                key.activate()
            elif key.active():
                key.deactivate()
            chars.append(key.draw())
        chars.append(Delimiter().draw())

        output = self.keyboard_header(size)

        for i in range(len(chars[0])):
            line = ""
            for j in range(len(chars)):
                line += chars[j][i]
            output.append(line)

        return output

    def handle_message(self, msg):
        # try:
        #     self.channel = msg.channel
        # except:
        #     pass
        self.midi_state.handle_message(msg)


class MidiPiano(Thread):

    def __init__(self, port_in_name, port_out_name, octaves=4, callback=None):
        super(MidiPiano, self).__init__()
        self.port_in_name = port_in_name
        self.port_out_name = port_out_name
        self.callback = callback
        self.octaves = octaves
        self.keyboard = Keyboard(octaves)
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

        # Set the callback and go live
        self.port_in.callback = self.handle_message

        # Enter keep-alive main loop
        while True:
            if self.stopped():
                self.shutdown()
                break
            sleep(1)

    def handle_message(self, msg):
        self.keyboard.handle_message(msg)

        print("\033c")
        print("\n".join(self.keyboard.draw()))

        if self.callback:
            self.callback(msg)

        self.port_out.send(msg)


open_ports = []


def main():
    # Detect if fluidsynth is running. If it is, connect to it, otherwise
    # create a virtual port.
    fluidsynth = [port for port in get_output_names()
                  if "fluid" in port.lower()]
    # harmonizer = MidiHarmonizer("vPort Harmonizer IN", "vPort Harmonizer OUT", melody_channel=1, chord_channel=2)
    # harmonizer.start()
    # open_ports.append(harmonizer)

    piano = MidiPiano("vPort Harmonizer OUT", "vPort Piano OUT", 6)
    piano.start()
    open_ports.append(piano)

    # recorder = MidiRecorder("vPort Piano OUT", fluidsynth[
    #                         0] if fluidsynth else "vPort Recorder OUT")
    # recorder.start()
    # open_ports.append(recorder)

    print("Started")


def signal_handler(signal, frame):
    for port in open_ports:
        port.stop()
    for port in open_ports:
        port.join()
    print("")
    print("Stopped")

if __name__ == '__main__':
    # Catch Ctrl+C to allow for clean program exit
    signal(SIGINT, signal_handler)
    main()
