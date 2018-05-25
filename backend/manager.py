"""
The central hub for administrating the application.
"""

### System ###
import logging

### Magenta ###
from magenta.models.drums_rnn import drums_rnn_sequence_generator
from magenta.models.melody_rnn import melody_rnn_sequence_generator
from magenta.models.polyphony_rnn import polyphony_sequence_generator
from magenta.models.performance_rnn import performance_sequence_generator
from magenta.models.pianoroll_rnn_nade import pianoroll_rnn_nade_sequence_generator
from magenta.music.sequence_generator_bundle import read_bundle_file, GeneratorBundleParseException

### Local ###
from settings import *  # pylint: disable-msg=wildcard-import, unused-wildcard-import
from .song import Song, SongPart
from middleware.virtual_keyboard import Keyboard
from middleware import MidiHarmonizer, MidiRecorder
from midi_interface import SongStructureMidiInteraction

### Globals ###
GENERATOR_MAP = melody_rnn_sequence_generator.get_generator_map()
GENERATOR_MAP.update(drums_rnn_sequence_generator.get_generator_map())
GENERATOR_MAP.update(performance_sequence_generator.get_generator_map())
GENERATOR_MAP.update(pianoroll_rnn_nade_sequence_generator.get_generator_map())
GENERATOR_MAP.update(polyphony_sequence_generator.get_generator_map())


def load_generator_from_bundle_file(bundle_file):
    """loads a bundle file as a SequenceGenerator() object."""
    try:
        bundle = read_bundle_file(bundle_file)
    except GeneratorBundleParseException:
        logging.warning("Failed to parse '{}'".format(bundle_file))
        return None

    generator_id = bundle.generator_details.id  # pylint: disable-msg=no-member
    if generator_id not in GENERATOR_MAP:
        logging.warning("Unrecognized SequenceGenerator ID '{}' in '{}'".format(generator_id, bundle_file))
        return None

    generator = GENERATOR_MAP[generator_id](checkpoint=None, bundle=bundle)
    generator.initialize()
    logging.info("Loaded '{}' generator bundle from file '{}'".format(
        bundle.generator_details.id, bundle_file))  # pylint: disable-msg=no-member
    return generator


def load_song(path):
    """Loads a .sng file as a Song() object"""
    structure = Song()
    chords_per_part = {}
    with open(path, "r") as file:
        for line in file:
            parts = [l.strip() for l in line.split(",")]
            song_part = SongPart(parts[0], parts[1:])
            if chords_per_part.get(song_part.name, False) and not song_part:
                song_part = chords_per_part[song_part.name]
            structure.append(song_part)
            chords_per_part[song_part.name] = song_part
    logging.info("Loaded '{}' with structure: {}".format(file, structure))
    return structure


class ComposerManager():

    def __init__(self):
        self.generators = []
        self.interaction = None
        self.harmonizer = None
        self.recorder = None
        self.input_port = None
        self.output_port = None
        self.selected_song = None
        self.keyboard_melody = Keyboard(channel=1, note_shift=-36)
        self.keyboard_bass = Keyboard(channel=2, note_shift=-12)

    def set_song(self, song):
        logging.info("Song set to '{}'".format(song))
        self.selected_song = song

    def note_callback(self, original_msg, new_msg):
        """An internal callback for displaying the currently active notes on a Keyboard() object."""
        # pylint: disable-msg=unused-argument
        self.keyboard_melody.handle_message(new_msg)
        self.keyboard_bass.handle_message(new_msg)

    def set_input_port(self, port):
        logging.info("Input port set to '{}'".format(port))
        self.input_port = port

    def set_output_port(self, port):
        logging.info("Output port set to '{}'".format(port))
        self.output_port = port

    def load_models(self):
        """Loads all required models files into a list of SequenceGenerator() objects"""
        for bundle_file in ["models/melody.mag", "models/bass.mag", "models/drums.mag"]:
            generator = load_generator_from_bundle_file(bundle_file)
            if generator:
                self.generators.append(generator)

    def start(self):
        """Sets up the signal chain and starts the generation process."""
        song = load_song(self.selected_song)
        self.start_harmonizer()
        self.start_recorder()
        self.start_interaction(song)
        return song.duration()

    def stop(self):
        """Stops the signal chain and thus the generation process."""
        self.stop_interaction()
        self.stop_harmonizer()
        self.stop_recorder()

    def start_interaction(self, song):
        """Initialises an interaction or starts it if it already exists and has not been stopped."""
        if not self.interaction:
            self.interaction = SongStructureMidiInteraction(self.generators, 120,
                                                            tick_duration=4 * (60.0 / 120), structure=song)
        if self.interaction and not self.interaction.stopped() and not self.interaction.is_alive():
            logging.info("Started MIDI interaction")
            self.interaction.start()

    def stop_interaction(self):
        """Stops an interaction if it exists and deletes the object since Threads can not be restarted."""
        if self.interaction and self.interaction.is_alive():
            logging.debug("Stopping MIDI interaction")
            self.interaction.stop()
            self.interaction.join()
            self.interaction = None
            logging.info("Stopped MIDI interaction")

    def start_recorder(self):
        """Initialises a recorder or starts it if it already exists and has not been stopped."""
        if not self.recorder:
            self.recorder = MidiRecorder(
                HARMONIZER_OUTPUT_NAME, self.output_port if self.output_port else RECORDER_OUTPUT_NAME)
        if self.recorder and not self.recorder.stopped() and not self.recorder.is_alive():
            logging.info("Started MIDI recorder")
            self.recorder.start()

    def stop_recorder(self):
        """Stops a recorder if it exists and deletes the object since Threads can not be restarted."""
        if self.recorder and self.recorder.is_alive():
            logging.debug("Stopping MIDI recorder")
            self.recorder.stop()
            self.recorder.join()
            self.recorder = None
            logging.info("Stopped MIDI recorder")

    def start_harmonizer(self):
        """Initialises a harmonizer or starts it if it already exists and has not been stopped."""
        if not self.harmonizer:
            self.harmonizer = MidiHarmonizer(HARMONIZER_INPUT_NAME, HARMONIZER_OUTPUT_NAME, callback=self.note_callback)
        if self.harmonizer and not self.harmonizer.stopped() and not self.harmonizer.is_alive():
            logging.info("Started MIDI harmonizer")
            self.harmonizer.start()

    def stop_harmonizer(self):
        """Stops a harmonizer if it exists and deletes the object since Threads can not be restarted."""
        if self.harmonizer and self.harmonizer.is_alive():
            logging.debug("Stopping MIDI harmonizer")
            self.harmonizer.stop()
            self.harmonizer.join()
            self.harmonizer = None
            logging.info("Stopped MIDI harmonizer")

    def reset(self):
        """Alias for stop()."""
        self.stop()
