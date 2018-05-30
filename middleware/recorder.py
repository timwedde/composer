### System ###
from time import sleep, time
from threading import Thread, Event
from decimal import Decimal, ROUND_DOWN, localcontext

### Mido ###
from mido.midifiles.tracks import _to_reltime
from mido.midifiles.units import second2tick, bpm2tempo
from mido import open_input, open_output, get_input_names, get_output_names, MidiFile, MidiTrack, Message # pylint: disable-msg=no-name-in-module, line-too-long


class MidiRecorder(Thread):
    """
    Records incoming MIDI messages into a properly-formed MIDI file.
    Also functions as a relay.
    """

    def __init__(self, port_in_name, port_out_name, callback=None):
        super(MidiRecorder, self).__init__()
        self.port_in_name = port_in_name
        self.port_in = None
        self.port_out_name = port_out_name
        self.port_out = None
        self.callback = callback
        self.first_time = None
        self.tracks = [MidiTrack(), MidiTrack(), MidiTrack(), MidiTrack()]
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

        self.port_out.send(Message(type="program_change", program=57, channel=1))
        self.port_out.send(Message(type="program_change", program=68, channel=2))
        self.port_out.send(Message(type="program_change", program=1, channel=3))

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
            tm = float(Decimal(time()).quantize(Decimal("0.001"), rounding=ROUND_DOWN))
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
        except IndexError:
            pass

        if self.callback:
            self.callback(msg)

        self.port_out.send(msg)
