# Architecture:
#                      GUI
#                       |
#     __________________|__________________
#     |           |           |           |
# Magenta -> Harmonizer -> Recorder -> Synthesizer
#                 |           |
#             vKeyboard   MIDI File

import logging
logging.basicConfig(filename="output.log", level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

import os
import sys
import math
import time
import urwid
from glob import glob
from signal import signal, SIGINT

from song import SongPart
from mido import get_input_names, get_output_names
from midi_interface.midi_hub import MidiHub, TextureType
from port_relay import MidiHarmonizer, MidiRecorder, Keyboard
from midi_interface.midi_interaction import SongStructureMidiInteraction

logging.debug("Importing magenta packages")
from magenta.music import sequence_generator_bundle
from magenta.models.drums_rnn import drums_rnn_sequence_generator
from magenta.models.melody_rnn import melody_rnn_sequence_generator
from magenta.models.polyphony_rnn import polyphony_sequence_generator
from magenta.models.performance_rnn import performance_sequence_generator
from magenta.models.pianoroll_rnn_nade import pianoroll_rnn_nade_sequence_generator
logging.debug("Done importing magenta packages")

_GENERATOR_MAP = melody_rnn_sequence_generator.get_generator_map()
_GENERATOR_MAP.update(drums_rnn_sequence_generator.get_generator_map())
_GENERATOR_MAP.update(performance_sequence_generator.get_generator_map())
_GENERATOR_MAP.update(pianoroll_rnn_nade_sequence_generator.get_generator_map())
_GENERATOR_MAP.update(polyphony_sequence_generator.get_generator_map())

UPDATE_INTERVAL = 0.2

HARMONIZER_INPUT_NAME = "vPort Harmonizer IN"
HARMONIZER_OUTPUT_NAME = "vPort Harmonizer OUT"

RECORDER_INPUT_NAME = "vPort Recorder IN"
RECORDER_OUTPUT_NAME = "vPort Recorder OUT"

def sin100(x):
    # A sin function that returns values between 0 and 100 and repeats after x == 100.
    return 50 + 50 * math.sin(x * math.pi / 50)

def load_generator_from_bundle_file(bundle_file):
    try:
        bundle = sequence_generator_bundle.read_bundle_file(bundle_file)
    except sequence_generator_bundle.GeneratorBundleParseException:
        logging.warn("Failed to parse '{}'".format(bundle_file))
        return None

    generator_id = bundle.generator_details.id
    if generator_id not in _GENERATOR_MAP:
        logging.warn("Unrecognized SequenceGenerator ID '{}' in '{}'".format(generator_id, bundle_file))
        return None

    generator = _GENERATOR_MAP[generator_id](checkpoint=None, bundle=bundle)
    generator.initialize()
    logging.info("Loaded '{}' generator bundle from file '{}'".format(bundle.generator_details.id, bundle_file))
    return generator

class AppModel:
    """
    A class responsible for storing the data that will be displayed
    on the graph, and keeping track of which mode is enabled.
    """

    data_max_value = 100

    def __init__(self):
        data = [ ("Saw", list(range(0, 100, 2)) * 2),
            ("Square", [0] * 30 + [100] * 30),
            ("Sine 1", [ sin100(x) for x in range(100)]),
            ("Sine 2", [(sin100(x) + sin100(x * 2)) / 2 for x in range(100)]),
            ("Sine 3", [(sin100(x) + sin100(x * 3)) / 2 for x in range(100)]),
            ]
        self.input_port = None
        self.output_port = None
        self.song = None
        self.keyboard = Keyboard(octaves=4, channel=1, note_shift=-36)
        self.modes = []
        self.data = {}
        for m, d in data:
            self.modes.append(m)
            self.data[m] = d

    def get_modes(self):
        return self.modes

    def set_mode(self, m):
        self.current_mode = m

    def set_song(self, song):
        logging.info(f"Song set to '{song}'")
        self.song = song

    def set_input_port(self, port):
        logging.info(f"Input port set to '{port}'")
        self.input_port = port

    def set_output_port(self, port):
        logging.info(f"Output port set to '{port}'")
        self.output_port = port

    def note_callback(self, original_msg, new_msg):
        self.keyboard.handle_message(new_msg)

    def get_data(self, offset, r):
        """
        Return the data in [offset:offset + r], the maximum value
        for items returned, and the offset at which the data
        repeats.
        """
        l = []
        d = self.data[self.current_mode]
        while r:
            offset = offset % len(d)
            segment = d[offset:offset + r]
            r -= len(segment)
            offset += len(segment)
            l += segment
        return l, self.data_max_value, len(d)

class AppView(urwid.WidgetWrap):
    # A class responsible for providing the application"s interface and graph display.
    palette = [
        ("body",          "black",        "light gray", "standout"),
        ("header",        "white",        "dark red",   "bold"),
        ("screen edge",   "light blue",   "dark cyan"),
        ("main shadow",   "dark gray",    "black"),
        ("line",          "black",        "light gray", "standout"),
        ("bg background", "light gray",   "black"),
        ("bg 1",          "black",        "dark blue", "standout"),
        ("bg 1 smooth",   "dark blue",    "black"),
        ("bg 2",          "black",        "dark cyan", "standout"),
        ("bg 2 smooth",   "dark cyan",    "black"),
        ("button normal", "light gray",   "dark blue", "standout"),
        ("button select", "white",        "dark green"),
        ("line",          "black",        "light gray", "standout"),
        ("pg normal",     "white",        "black", "standout"),
        ("pg complete",   "white",        "dark magenta"),
        ("pg smooth",     "dark magenta", "black"),
        ("bigtext",       "white",        "black"),
        ]

    graph_samples_per_bar = 10
    graph_num_bars = 5 # 12 * 5
    graph_offset_per_second = 5

    def __init__(self, controller):
        self.controller = controller
        self.started = False
        self.start_time = None
        self.offset = 0
        self.last_offset = None
        urwid.WidgetWrap.__init__(self, self.main_window())

    def get_offset_now(self):
        if self.start_time is None:
            return 0
        if not self.started:
            return self.offset
        tdelta = time.time() - self.start_time
        return int(self.offset + (tdelta * self.graph_offset_per_second))

    def update_graph(self, force_update=False):
        self.pud._invalidate()
        o = self.get_offset_now()
        if o == self.last_offset and not force_update:
            return False
        self.last_offset = o
        gspb = self.graph_samples_per_bar
        r = gspb * self.graph_num_bars
        d, max_value, repeat = self.controller.get_data(o, r)
        l = []
        for n in range(self.graph_num_bars):
            value = sum(d[n * gspb:(n + 1) * gspb]) / gspb
            # toggle between two bar types
            if n & 1:
                l.append([0, value])
            else:
                l.append([value, 0])
        # self.graph.set_data(l, max_value)
        self.graph_1.set_data(l, max_value)
        self.graph_2.set_data(l, max_value)

        # also update progress
        if (o // repeat) & 1:
            # show 100% for first half, 0 for second half
            if o % repeat > repeat // 2:
                prog = 0
            else:
                prog = 1
        else:
            prog = float(o % repeat) / repeat
        self.animate_progress.set_completion(prog)
        return True

    def on_start_button(self, button):
        if self.started:
            button.set_label("Start")
            self.offset = self.get_offset_now()
            self.started = False
            self.controller.stop_animation()
            self.controller.stop_interaction()
            self.controller.stop_harmonizer()
            self.controller.stop_recorder()
        else:
            button.set_label("Stop")
            self.started = True
            self.start_time = time.time()
            self.controller.start_harmonizer()
            self.controller.start_recorder()
            self.controller.start_interaction()
            self.controller.animate_graph()

    def on_reset_button(self, w):
        self.offset = 0
        self.start_time = time.time()
        self.update_graph(True)
        self.started = True
        self.on_start_button(self.start_button)
        self.controller.reset()

    def on_input_port_button(self, button, state):
        if state:
            self.controller.set_input_port(button.get_label())

    def on_input_port_change(self, port):
        for b in self.input_port_buttons:
            if b.get_label() == port:
                b.set_state(True, do_callback=False)
                break

    def on_output_port_button(self, button, state):
        if state:
            self.controller.set_output_port(button.get_label())

    def on_output_port_change(self, port):
        for b in self.output_port_buttons:
            if b.get_label() == port:
                b.set_state(True, do_callback=False)
                break

    def on_song_button(self, button, state):
        if state:
            self.controller.set_song(button.get_label())

    def on_song_change(self, song):
        for rb in self.song_buttons:
            if rb.get_label() == song:
                rb.set_state(True, do_callback=False)
                break

    def on_mode_button(self, button, state):
        """Notify the controller of a new mode setting."""
        if state:
            # The new mode is the label of the button
            self.controller.set_mode(button.get_label())
        self.last_offset = None

    def on_mode_change(self, m):
        """Handle external mode change by updating radio buttons."""
        for rb in self.mode_buttons:
            if rb.get_label() == m:
                rb.set_state(True, do_callback=False)
                break
        self.last_offset = None

    def on_unicode_checkbox(self, w, state):
        logging.info("{} Unicode Graphics".format("Enabled" if state else "Disabled"))
        self.graph = self.bar_graph(state)
        self.graph_wrap._w = self.graph
        self.graph_wrap_1._w = self.graph_1
        self.graph_wrap_2._w = self.graph_2
        self.animate_progress = self.progress_bar(state)
        self.animate_progress_wrap._w = self.animate_progress
        self.update_graph(True)

    def on_chord_passthrough_checkbox(self, w, state):
        logging.info("{} Chord Passthrough".format("Enabled" if state else "Disabled"))
        self.controller.chord_passthrough(state)

    def main_shadow(self, w, shadow=True):
        """Wrap a shadow and background around widget w."""
        bg = urwid.AttrWrap(urwid.SolidFill(u"\u2592"), "screen edge")

        if shadow:
            shadow = urwid.AttrWrap(urwid.SolidFill(u" "), "main shadow")
            bg = urwid.Overlay( shadow, bg,
                ("fixed left", 3), ("fixed right", 1),
                ("fixed top", 2), ("fixed bottom", 1))
            w = urwid.Overlay( w, bg,
                ("fixed left", 2), ("fixed right", 3),
                ("fixed top", 1), ("fixed bottom", 2))
        else:
            w = urwid.Overlay( w, bg,
                ("fixed left", 1), ("fixed right", 1),
                ("fixed top", 1), ("fixed bottom", 1))
        return w

    def bar_graph(self, smooth=False, flipped=False):
        satt = None
        if smooth:
            satt = {(1,0): "bg 1 smooth", (2,0): "bg 2 smooth"}
        w = urwid.BarGraph(["bg background","bg 1","bg 2"], satt=satt)
        return w

    def button(self, t, fn):
        w = urwid.Button(t, fn)
        w = urwid.AttrWrap(w, "button normal", "button select")
        return w

    def radio_button(self, g, l, fn):
        w = urwid.RadioButton(g, l, False, on_state_change=fn)
        w = urwid.AttrWrap(w, "button normal", "button select")
        return w

    def progress_bar(self, smooth=False):
        if smooth:
            return urwid.ProgressBar("pg normal", "pg complete", 0, 1, "pg smooth")
        else:
            return urwid.ProgressBar("pg normal", "pg complete", 0, 1)

    def graph_controls(self):
        modes = self.controller.get_modes()
        # setup mode radio buttons
        self.mode_buttons = []
        group = []
        for m in modes:
            rb = self.radio_button(group, m, self.on_mode_button)
            self.mode_buttons.append(rb)

        songs = self.controller.list_songs()
        self.song_buttons = []
        group = []
        for song in songs:
            rb = self.radio_button(group, song, self.on_song_button)
            self.song_buttons.append(rb)

        # setup animate button
        self.start_button = self.button("Start", self.on_start_button)

        # TODO: make this into its own method
        self.started = False
        self.controller.stop_animation()
        self.controller.stop_interaction()
        self.controller.stop_harmonizer()
        self.controller.stop_recorder()

        self.offset = 0
        self.animate_progress = self.progress_bar()
        animate_controls = urwid.GridFlow([self.start_button, self.button("Reset", self.on_reset_button)], 9, 2, 0, "center")

        if urwid.get_encoding_mode() == "utf8":
            unicode_checkbox = urwid.CheckBox("Enable Unicode Graphics", on_state_change=self.on_unicode_checkbox)
        else:
            unicode_checkbox = urwid.Text("UTF-8 encoding not detected")

        chord_passthrough = urwid.CheckBox("Chord Passthrough", state=True, on_state_change=self.on_chord_passthrough_checkbox)

        self.animate_progress_wrap = urwid.WidgetWrap(self.animate_progress)

        # setup MIDI I/O radio buttons
        self.input_port_buttons = []
        group = []
        for port in self.controller.get_input_ports():
            b = self.radio_button(group, port, self.on_input_port_button)
            self.input_port_buttons.append(b)

        self.output_port_buttons = []
        group = []
        for port in self.controller.get_output_ports():
            b = self.radio_button(group, port, self.on_output_port_button)
            self.output_port_buttons.append(b)

        ipb = [urwid.Text("No MIDI Input Ports available", align="center")]
        if self.input_port_buttons:
            ipb = [urwid.Text("MIDI Input Port", align="center")] + self.input_port_buttons

        opb = [urwid.Text("No MIDI Output Ports available", align="center")]
        if self.output_port_buttons:
            opb = [urwid.Text("MIDI Output Port", align="center")] + self.output_port_buttons

        l = [urwid.Text("Song", align="center")
            ] + self.song_buttons + ([urwid.Divider()] + ipb if ipb else []) + ([urwid.Divider()] + opb if opb else []) + [
            urwid.Divider(),
            urwid.Text("Animation",align="center"),
            animate_controls,
            self.animate_progress_wrap,
            urwid.Divider(),
            urwid.LineBox(unicode_checkbox),
            urwid.LineBox(chord_passthrough),
            urwid.Divider(),
            self.button("Quit", self.controller.exit_program)]
        w = urwid.ListBox(urwid.SimpleListWalker(l))
        return w

    def main_window(self):
        self.graph = self.bar_graph()
        self.graph_wrap = urwid.WidgetWrap(self.graph)

        self.graph_1 = self.bar_graph()
        self.graph_wrap_1 = urwid.WidgetWrap(self.graph_1)
        self.graph_2 = self.bar_graph()
        self.graph_wrap_2 = urwid.WidgetWrap(self.graph_2)

        vline = urwid.AttrMap(urwid.SolidFill("|"), "line")
        hline = urwid.AttrMap(urwid.SolidFill("-"), "line")

        # first graph
        nps_label = urwid.Text("Notes/s", align="center")
        nps_label = urwid.ListBox(urwid.SimpleListWalker([nps_label]))
        notes_per_second = urwid.Pile([("fixed", 1, nps_label), ("weight", 2, self.graph_wrap_1)])

        # second graph
        pr_label = urwid.Text("Piano Roll", align="center")
        pr_label = urwid.ListBox(urwid.SimpleListWalker([pr_label]))
        piano_roll = urwid.Pile([("fixed", 1, pr_label), ("weight", 2, self.graph_wrap_2)])

        # graph box
        # graphs = urwid.Pile([("weight", 2, notes_per_second), ("fixed", 1, hline), ("weight", 2, piano_roll)])
        self.pud = Pudding(self.controller.model)
        l = urwid.ListBox(urwid.SimpleListWalker([self.pud]))
        graphs = urwid.Pile([("weight", 2, l), ("fixed", 1, hline), ("weight", 2, piano_roll)])

        # side panel
        controls = self.graph_controls()

        # graph box + side panel
        window = urwid.Columns([("weight", 2, graphs), ("fixed", 1, vline), controls], dividechars=1, focus_column=2)

        window = urwid.Padding(window,("fixed left",1),("fixed right",0))
        window = urwid.AttrMap(window,"body")
        window = urwid.LineBox(window)
        window = urwid.AttrMap(window,"line")

        window = self.main_shadow(window, False)

        return window

class Pudding(urwid.Widget):
    _sizing = frozenset(['flow'])

    def __init__(self, model):
        self.model = model

    def rows(self, size, focus=False):
        return 9

    def render(self, size, focus=False):
        (maxcol,) = size
        data = self.model.keyboard.draw()
        for i in range(len(data)):
            data[i] = data[i].encode()
        return urwid.TextCanvas(data, maxcol=maxcol)

class AppController:
    # A class responsible for setting up the model and view and running the application.
    def __init__(self):
        self.animate_alarm = None
        self.model = AppModel()
        self.harmonizer = None # place for MidiHarmonizer
        self.recorder = None # place for MidiRecorder
        self.interaction = None # place for MidiInteraction
        self.generators = [] # list of available generators
        self.view = AppView(self)
        self.reset() # initialize the view
        self.load_magenta()

    def load_song(self, file):
        structure = []
        chords_per_part = {}
        with open(file, "r") as f:
            for line in f:
                parts = [l.strip() for l in line.split(",")]
                sp = SongPart(parts[0], parts[1:])
                if chords_per_part.get(sp.name, False) and not sp.chords:
                    sp.chords = chords_per_part[sp.name]
                structure.append(sp)
                chords_per_part[sp.name] = sp.chords
        logging.info(f"Loaded '{file}' with structure: {structure}")
        return structure

    def list_songs(self):
        if not os.path.exists("songs"):
            os.mkdir("songs")
            with open("songs/song_1.sng", "w") as f:
                f.write("INTRO\nCHORUS\nVERSE\nCHORUS\nVERSE\nOUTRO")
        songs = []
        for file in glob("songs/*.sng"):
            logging.info(f"Found {file}")
            songs.append(file)
        return songs

    def load_magenta(self):
        # TODO: do not hardcode bundle paths
        # TODO: handle case where invalid generator config is encountered, e.g some/all missing
        for bundle_file in ["models/bass.mag", "models/melody.mag", "models/drums.mag"]:
            generator = load_generator_from_bundle_file(bundle_file)
            if generator:
                self.generators.append(generator)

    def chord_passthrough(self, state):
        if self.interaction:
            self.interaction.chord_passthrough = state

    def start_interaction(self):
        if not self.interaction:
            self.melody_hub = MidiHub(None, [HARMONIZER_INPUT_NAME], TextureType.POLYPHONIC, playback_channel=1)
            self.bass_hub = MidiHub(None, [HARMONIZER_INPUT_NAME], TextureType.POLYPHONIC, playback_channel=2)
            self.drums_hub = MidiHub(None, [HARMONIZER_INPUT_NAME], TextureType.POLYPHONIC, playback_channel=9)
            structure = self.load_song(self.model.song)
            self.interaction = SongStructureMidiInteraction(self.melody_hub, self.bass_hub, self.drums_hub, self.generators, 120, tick_duration=4 * (60.0 / 120), structure=structure, chord_passthrough=True)
        if self.interaction and not self.interaction.stopped() and not self.interaction.is_alive():
            logging.info("Started MIDI interaction")
            self.interaction.start()

    def stop_interaction(self):
        if self.interaction and self.interaction.is_alive():
            logging.debug("Stopping MIDI interaction")
            self.interaction.stop()
            self.interaction.join()
            self.interaction = None
            self.melody_hub = None
            self.bass_hub = None
            self.drums_hub = None
            logging.info("Stopped MIDI interaction")

    def start_recorder(self):
        if not self.recorder:
            self.recorder = MidiRecorder(HARMONIZER_OUTPUT_NAME, self.model.output_port if self.model.output_port else RECORDER_OUTPUT_NAME)
        if self.recorder and not self.recorder.stopped() and not self.recorder.is_alive():
            logging.info("Started MIDI recorder")
            self.recorder.start()

    def stop_recorder(self):
        if self.recorder and self.recorder.is_alive():
            logging.debug("Stopping MIDI recorder")
            self.recorder.stop()
            self.recorder.join()
            self.recorder = None
            logging.info("Stopped MIDI recorder")

    def start_harmonizer(self):
        if not self.harmonizer:
            self.harmonizer = MidiHarmonizer(HARMONIZER_INPUT_NAME, HARMONIZER_OUTPUT_NAME, callback=self.model.note_callback)
        if self.harmonizer and not self.harmonizer.stopped() and not self.harmonizer.is_alive():
            logging.info("Started MIDI harmonizer")
            self.harmonizer.start()

    def stop_harmonizer(self):
        if self.harmonizer and self.harmonizer.is_alive():
            logging.debug("Stopping MIDI harmonizer")
            self.harmonizer.stop()
            self.harmonizer.join()
            self.harmonizer = None
            logging.info("Stopped MIDI harmonizer")

    def reset(self):
        self.stop_interaction()
        self.stop_harmonizer()
        self.stop_recorder()
        modes = self.get_modes()
        if modes:
            self.model.set_mode(modes[0])
            self.view.on_mode_change(modes[0])

        songs = self.list_songs()
        if songs:
            self.model.set_song(songs[0])
            self.view.on_song_change(songs[0])

        in_port = self.get_input_ports()
        if in_port:
            self.model.set_input_port(in_port[0])
            self.view.on_input_port_change(in_port[0])

        out_port = self.get_output_ports()
        if out_port:
            self.model.set_output_port(out_port[0])
            self.view.on_output_port_change(out_port[0])

        self.view.update_graph(True)

    def get_input_ports(self):
        return get_input_names()

    def get_output_ports(self):
        return get_output_names()

    def set_input_port(self, port):
        self.model.set_input_port(port)

    def set_output_port(self, port):
        self.model.set_output_port(port)

    def get_modes(self):
        """Allow our view access to the list of modes."""
        return self.model.get_modes()

    def set_mode(self, m):
        """Allow our view to set the mode."""
        rval = self.model.set_mode(m)
        self.view.update_graph(True)
        return rval

    def set_song(self, song):
        self.model.set_song(song)

    def get_data(self, offset, range):
        """Provide data to our view for the graph."""
        return self.model.get_data( offset, range )

    def animate_graph(self, loop=None, user_data=None):
        self.view.update_graph()
        self.animate_alarm = self.loop.set_alarm_in(UPDATE_INTERVAL, self.animate_graph)

    def stop_animation(self):
        if self.animate_alarm:
            self.loop.remove_alarm(self.animate_alarm)
        self.animate_alarm = None

    def exit_program(self, w=None):
        self.stop_interaction()
        self.stop_harmonizer()
        self.stop_recorder()
        raise urwid.ExitMainLoop()

    def unhandled_input(self, key):
        if key in ["q", "Q"]:
            self.exit_program()

    def main(self):
        self.loop = urwid.MainLoop(self.view, self.view.palette, unhandled_input=self.unhandled_input)
        self.loop.run()

def main():
    global gc
    gc = AppController()
    gc.main()

def signal_handler(signal, frame):
    logging.info("Received SIGINT, stopping...")
    gc.stop_interaction()
    gc.stop_harmonizer()
    gc.stop_recorder()
    raise urwid.ExitMainLoop

def my_handler(type, value, tb):
    logger.exception("Uncaught exception: {0}".format(str(value)))

if __name__ == "__main__":
    signal(SIGINT, signal_handler)
    sys.excepthook = my_handler
    main()
    logging.info("Done")
