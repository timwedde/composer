"""
An ncurses-based terminal frontend.
"""

### System ###
import os
import logging
from time import time
from glob import glob

### Packages ###
import urwid

### Mido ###
from mido import get_input_names, get_output_names  # pylint: disable-msg=no-name-in-module

### Local ###
from backend import ComposerManager
from backend.song import load_song

### Globals ###
from settings import UPDATE_INTERVAL


SONG_MAP = {}


def list_songs():
    if not os.path.exists("songs"):
        os.mkdir("songs")
        with open("songs/song_1.sng", "w") as file:
            file.write("INTRO\nCHORUS\nVERSE\nCHORUS\nVERSE\nOUTRO")
    songs = []
    for file in glob("songs/*.sng"):
        songs.append((load_song(file), os.path.basename(file)))
    return songs


def song_to_title(song):
    song, filename = song
    title = "{}{}".format(song.name if song.name else filename, " by {}".format(song.author) if song.author else "")
    return title


def window_shadow(window, shadow=False):
    background = urwid.AttrWrap(urwid.SolidFill(u"\u2592"), "screen edge")
    if shadow:
        shadow = urwid.AttrWrap(urwid.SolidFill(u" "), "main shadow")
        background = urwid.Overlay(shadow, background,
                                   ("fixed left", 3), ("fixed right", 1),
                                   ("fixed top", 2), ("fixed bottom", 1))
        window = urwid.Overlay(window, background,
                               ("fixed left", 2), ("fixed right", 3),
                               ("fixed top", 1), ("fixed bottom", 2))
    else:
        window = urwid.Overlay(window, background,
                               ("fixed left", 1), ("fixed right", 1),
                               ("fixed top", 1), ("fixed bottom", 1))
    return window


def make_button(title, callback):
    button = urwid.Button(title, callback)
    return urwid.AttrWrap(button, "button normal", "button select")


def make_radio_button(group, title, callback):
    radio_button = urwid.RadioButton(group, title, False, on_state_change=callback)
    return urwid.AttrWrap(radio_button, "button normal", "button select")


def make_progress_bar(smooth=False):
    if smooth:
        return urwid.ProgressBar("pg normal", "pg complete", 0, 1, "pg smooth")
    return urwid.ProgressBar("pg normal", "pg complete", 0, 1)


class KeyboardWrap(urwid.Widget):
    _sizing = frozenset(['flow'])

    def __init__(self, keyboard):
        self.keyboard = keyboard

    def rows(self, size, focus=False):
        # pylint: disable-msg=no-self-use
        # pylint: disable-msg=unused-argument
        return 9

    def render(self, size, focus=False):
        # pylint: disable-msg=unused-argument
        (maxcol,) = size
        data = self.keyboard.draw(maxcol)
        data = [d.encode() for d in data]
        return urwid.TextCanvas(data, maxcol=maxcol)


class TerminalGUI(urwid.WidgetWrap):
    palette = [
        ("body", "black", "light gray", "standout"),
        ("header", "white", "dark red", "bold"),
        ("screen edge", "light blue", "dark cyan"),
        ("main shadow", "dark gray", "black"),
        ("line", "black", "light gray", "standout"),
        ("bg background", "light gray", "black"),
        ("bg 1", "black", "dark blue", "standout"),
        ("bg 1 smooth", "dark blue", "black"),
        ("bg 2", "black", "dark cyan", "standout"),
        ("bg 2 smooth", "dark cyan", "black"),
        ("button normal", "light gray", "dark blue", "standout"),
        ("button select", "white", "dark green"),
        ("line", "black", "light gray", "standout"),
        ("pg normal", "white", "black", "standout"),
        ("pg complete", "white", "dark magenta"),
        ("pg smooth", "dark magenta", "black"),
        ("bigtext", "white", "black"),
    ]

    def __init__(self):
        self.started = False
        self.current_song_started = None
        self.current_song_duration = None
        self.animate_alarm = None
        self.animate_progress = None
        self.animate_progress_wrap = None
        self.start_button = None
        self.loop = None
        self.song_buttons = []
        self.input_port_buttons = []
        self.output_port_buttons = []
        self.composer = ComposerManager()
        self.composer.load_models()
        for song in list_songs():
            SONG_MAP[song_to_title(song)] = song[0]
        urwid.WidgetWrap.__init__(self, self.main_window())
        self.reset()  # initialize the view after the window is rendered

    def on_start_button(self, button):
        if self.started:
            button.set_label("Start")
            self.started = False
            self.stop_refresh()
            self.composer.stop()
            self.reset()
        else:
            button.set_label("Stop")
            self.started = True
            self.refresh()
            self.current_song_duration = self.composer.start()
            self.current_song_started = time()

    def on_reset_button(self, window):
        # pylint: disable-msg=unused-argument
        self.started = True
        self.on_start_button(self.start_button)
        self.reset()

    def on_input_port_button(self, button, state):
        if state:
            self.composer.set_input_port(button.get_label())

    def on_input_port_change(self, port):
        for button in self.input_port_buttons:
            if button.get_label() == port:
                button.set_state(True, do_callback=False)
                break

    def on_output_port_button(self, button, state):
        if state:
            self.composer.set_output_port(button.get_label())

    def on_output_port_change(self, port):
        for button in self.output_port_buttons:
            if button.get_label() == port:
                button.set_state(True, do_callback=False)
                break

    def on_song_button(self, button, state):
        if state:
            self.composer.set_song(SONG_MAP[button.get_label()])

    def on_song_change(self, song):
        for radio_button in self.song_buttons:
            if radio_button.get_label() == song_to_title(song):
                radio_button.set_state(True, do_callback=False)
                break

    def on_unicode_checkbox(self, window, state):
        # pylint: disable-msg=unused-argument
        logging.info("{} Unicode Graphics".format("Enabled" if state else "Disabled"))
        self.animate_progress = make_progress_bar(state)
        self.animate_progress_wrap._w = self.animate_progress  # pylint: disable-msg=protected-access
        self.update_screen()

    def controls(self):
        songs = list_songs()
        self.song_buttons = []
        group = []
        for song in songs:
            title = song_to_title(song)
            radio_button = make_radio_button(group, title if title else filename, self.on_song_button)
            self.song_buttons.append(radio_button)

        # setup animate button
        self.start_button = make_button("Start", self.on_start_button)

        self.started = False
        self.composer.stop()

        self.animate_progress = make_progress_bar()
        animate_controls = urwid.GridFlow([self.start_button, make_button("Reset", self.on_reset_button)],
                                          9, 2, 0, "center")

        self.animate_progress_wrap = urwid.WidgetWrap(self.animate_progress)

        if urwid.get_encoding_mode() == "utf8":
            unicode_checkbox = urwid.CheckBox("Enable Unicode Graphics",
                                              on_state_change=self.on_unicode_checkbox)
        else:
            unicode_checkbox = urwid.Text("UTF-8 encoding not detected")

        # setup MIDI I/O radio buttons
        self.input_port_buttons = []
        group = []
        for port in get_input_names():
            radio_button = make_radio_button(group, port, self.on_input_port_button)
            self.input_port_buttons.append(radio_button)

        self.output_port_buttons = []
        group = []
        for port in get_output_names():
            radio_button = make_radio_button(group, port, self.on_output_port_button)
            self.output_port_buttons.append(radio_button)

        ipb = [urwid.Text("No MIDI Input Ports available", align="center")]
        if self.input_port_buttons:
            ipb = [urwid.Text("MIDI Input Port", align="center")] + self.input_port_buttons

        opb = [urwid.Text("No MIDI Output Ports available", align="center")]
        if self.output_port_buttons:
            opb = [urwid.Text("MIDI Output Port", align="center")] + self.output_port_buttons

        components = [urwid.Text("Song", align="center")] + \
            self.song_buttons + \
            ([urwid.Divider()] + ipb if ipb else []) + \
            ([urwid.Divider()] + opb if opb else []) + \
            [urwid.Divider(),
             urwid.Text("Generation", align="center"),
             animate_controls,
             self.animate_progress_wrap,
             urwid.Divider(),
             urwid.LineBox(unicode_checkbox),
             urwid.Divider(),
             make_button("Quit", self.exit_program)]
        return urwid.ListBox(urwid.SimpleListWalker(components))

    def main_window(self):
        vline = urwid.AttrMap(urwid.SolidFill("|"), "line")
        hline = urwid.AttrMap(urwid.SolidFill("-"), "line")

        # content box
        self.keyboard_melody = KeyboardWrap(self.composer.keyboard_melody)
        self.keyboard_bass = KeyboardWrap(self.composer.keyboard_bass)
        keyboard_melody_list = urwid.ListBox(
            urwid.SimpleListWalker([self.keyboard_melody]))
        keyboard_bass_list = urwid.ListBox(
            urwid.SimpleListWalker([self.keyboard_bass]))
        content_box = urwid.Pile([("weight", 2, keyboard_melody_list),
                                  ("fixed", 1, hline), ("weight", 2, keyboard_bass_list)])

        # side panel
        controls = self.controls()

        # content box + side panel
        window = urwid.Columns([("weight", 2, content_box),
                                ("fixed", 1, vline), controls],
                               dividechars=1, focus_column=2)

        window = urwid.Padding(window, ("fixed left", 1), ("fixed right", 0))
        window = urwid.AttrMap(window, "body")
        window = urwid.LineBox(window)
        window = urwid.AttrMap(window, "line")

        window = window_shadow(window)

        return window

    def reset(self):
        self.composer.stop()
        self.animate_progress.set_completion(0)
        self.current_song_duration = None
        self.current_song_started = None

        songs = list_songs()
        if songs:
            self.composer.set_song(songs[0][0])
            self.on_song_change(songs[0])

        in_port = get_input_names()
        if in_port:
            self.composer.set_input_port(in_port[0])
            self.on_input_port_change(in_port[0])

        out_port = get_output_names()
        if out_port:
            self.composer.set_output_port(out_port[0])
            self.on_output_port_change(out_port[0])

    def update_screen(self):
        self.keyboard_melody._invalidate()  # pylint: disable-msg=protected-access
        self.keyboard_bass._invalidate()  # pylint: disable-msg=protected-access
        if self.current_song_started:
            progress = ((time() - self.current_song_started) / 48)
            self.animate_progress.set_completion(progress)

    def refresh(self, loop=None, user_data=None):
        # pylint: disable-msg=unused-argument
        self.update_screen()
        self.animate_alarm = self.loop.set_alarm_in(UPDATE_INTERVAL, self.refresh)

    def stop_refresh(self):
        logging.info("Stopped")
        if self.animate_alarm:
            self.loop.remove_alarm(self.animate_alarm)
        self.animate_alarm = None

    def exit_program(self, window=None):
        # pylint: disable-msg=unused-argument
        self.composer.stop()
        raise urwid.ExitMainLoop()

    def unhandled_input(self, key):
        if key in ["q", "Q"]:
            self.exit_program()

    def main(self):
        self.loop = urwid.MainLoop(
            self, self.palette, unhandled_input=self.unhandled_input)
        self.loop.run()
