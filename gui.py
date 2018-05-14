#!/usr/bin/env python3

# Architecture:
#                      GUI
#                       |
#     __________________|__________________
#     |           |           |           |
# Magenta -> Harmonizer -> Recorder -> Synthesizer
#                 |           |
#             vKeyboard   MIDI File

### Logging ###
import logging
logging.basicConfig(filename="output.log", level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

### System ###
import os
import math
from time import time
import urwid
from glob import glob
from signal import signal, SIGINT

### Mido ###
from mido import get_input_names, get_output_names

### Local ###
from manager import ComposerManager

### Globals ###
UPDATE_INTERVAL = 0.2

def list_songs():
    if not os.path.exists("songs"):
        os.mkdir("songs")
        with open("songs/song_1.sng", "w") as f:
            f.write("INTRO\nCHORUS\nVERSE\nCHORUS\nVERSE\nOUTRO")
    songs = []
    for file in glob("songs/*.sng"):
        logging.info(f"Found {file}")
        songs.append(file)
    return songs

### GUI functions ###
def window_shadow(w, shadow=False):
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

def button(t, fn):
    w = urwid.Button(t, fn)
    w = urwid.AttrWrap(w, "button normal", "button select")
    return w

def radio_button(g, l, fn):
    w = urwid.RadioButton(g, l, False, on_state_change=fn)
    w = urwid.AttrWrap(w, "button normal", "button select")
    return w

def progress_bar(smooth=False):
    if smooth:
        return urwid.ProgressBar("pg normal", "pg complete", 0, 1, "pg smooth")
    else:
        return urwid.ProgressBar("pg normal", "pg complete", 0, 1)

class KeyboardWrap(urwid.Widget):
    _sizing = frozenset(['flow'])

    def __init__(self, composer):
        self.composer = composer

    def rows(self, size, focus=False):
        return 9

    def render(self, size, focus=False):
        (maxcol,) = size
        data = self.composer.keyboard.draw(maxcol)
        for i in range(len(data)):
            data[i] = data[i].encode()
        return urwid.TextCanvas(data, maxcol=maxcol)

class TerminalGUI(urwid.WidgetWrap):
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

    def __init__(self):
        self.started = False
        self.current_song_started = None
        self.current_song_duration = None
        self.animate_alarm = None
        self.song_buttons = []
        self.output_port_buttons = []
        self.composer = ComposerManager()
        self.composer.load_models()
        urwid.WidgetWrap.__init__(self, self.main_window())
        self.reset() # initialize the view after the window is rendered

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

    def on_reset_button(self, w):
        self.started = True
        self.on_start_button(self.start_button)
        self.reset()

    def on_input_port_button(self, button, state):
        if state:
            self.composer.set_input_port(button.get_label())

    def on_input_port_change(self, port):
        for b in self.input_port_buttons:
            if b.get_label() == port:
                b.set_state(True, do_callback=False)
                break

    def on_output_port_button(self, button, state):
        if state:
            self.composer.set_output_port(button.get_label())

    def on_output_port_change(self, port):
        for b in self.output_port_buttons:
            if b.get_label() == port:
                b.set_state(True, do_callback=False)
                break

    def on_song_button(self, button, state):
        if state:
            self.composer.set_song(button.get_label())

    def on_song_change(self, song):
        for rb in self.song_buttons:
            if rb.get_label() == song:
                rb.set_state(True, do_callback=False)
                break

    def on_chord_passthrough_checkbox(self, w, state):
        logging.info("{} Chord Passthrough".format("Enabled" if state else "Disabled"))
        self.composer.chord_passthrough(state)

    def on_unicode_checkbox(self, w, state):
        logging.info("{} Unicode Graphics".format("Enabled" if state else "Disabled"))
        self.animate_progress = progress_bar(state)
        self.animate_progress_wrap._w = self.animate_progress
        self.update_screen()

    def controls(self):
        songs = list_songs()
        self.song_buttons = []
        group = []
        for song in songs:
            rb = radio_button(group, song, self.on_song_button)
            self.song_buttons.append(rb)

        # setup animate button
        self.start_button = button("Start", self.on_start_button)

        self.started = False
        self.composer.stop()

        self.animate_progress = progress_bar()
        animate_controls = urwid.GridFlow([self.start_button, button("Reset", self.on_reset_button)], 9, 2, 0, "center")

        chord_passthrough = urwid.CheckBox("Chord Passthrough", state=True, on_state_change=self.on_chord_passthrough_checkbox)

        self.animate_progress_wrap = urwid.WidgetWrap(self.animate_progress)

        if urwid.get_encoding_mode() == "utf8":
            unicode_checkbox = urwid.CheckBox("Enable Unicode Graphics", on_state_change=self.on_unicode_checkbox)
        else:
            unicode_checkbox = urwid.Text("UTF-8 encoding not detected")

        # setup MIDI I/O radio buttons
        self.input_port_buttons = []
        group = []
        for port in get_input_names():
            b = radio_button(group, port, self.on_input_port_button)
            self.input_port_buttons.append(b)

        self.output_port_buttons = []
        group = []
        for port in get_output_names():
            b = radio_button(group, port, self.on_output_port_button)
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
            button("Quit", self.exit_program)]
        w = urwid.ListBox(urwid.SimpleListWalker(l))
        return w

    def main_window(self):
        vline = urwid.AttrMap(urwid.SolidFill("|"), "line")
        hline = urwid.AttrMap(urwid.SolidFill("-"), "line")

        # content box
        self.pud = KeyboardWrap(self.composer)
        l = urwid.ListBox(urwid.SimpleListWalker([self.pud]))
        content_box = urwid.Pile([("weight", 2, l), ("fixed", 1, hline), ("weight", 2, l)])

        # side panel
        controls = self.controls()

        # content box + side panel
        window = urwid.Columns([("weight", 2, content_box), ("fixed", 1, vline), controls], dividechars=1, focus_column=2)

        window = urwid.Padding(window,("fixed left",1),("fixed right",0))
        window = urwid.AttrMap(window,"body")
        window = urwid.LineBox(window)
        window = urwid.AttrMap(window,"line")

        window = window_shadow(window)

        return window

    def reset(self):
        self.composer.stop()
        self.animate_progress.set_completion(0)
        self.current_song_duration = None
        self.current_song_started = None

        songs = list_songs()
        if songs:
            self.composer.set_song(songs[0])
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
        self.pud._invalidate()
        if self.current_song_started:
            progress = ((time() - self.current_song_started) / 48)
            self.animate_progress.set_completion(progress)

    def refresh(self, loop=None, user_data=None):
        self.update_screen()
        self.animate_alarm = self.loop.set_alarm_in(UPDATE_INTERVAL, self.refresh)

    def stop_refresh(self):
        logging.info("Stopped")
        if self.animate_alarm:
            self.loop.remove_alarm(self.animate_alarm)
        self.animate_alarm = None

    def exit_program(self, w=None):
        self.composer.stop()
        raise urwid.ExitMainLoop()

    def unhandled_input(self, key):
        if key in ["q", "Q"]:
            self.exit_program()

    def main(self):
        self.loop = urwid.MainLoop(self, self.palette, unhandled_input=self.unhandled_input)
        self.loop.run()

def main():
    global app
    app = TerminalGUI()
    app.main()

def signal_handler(signal, frame):
    logging.info("Received SIGINT, stopping...")
    app.exit_program()

if __name__ == "__main__":
    signal(SIGINT, signal_handler)
    main()
    logging.info("Done")