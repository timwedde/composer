# Copyright 2017 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from time import time
from enum import Enum
from sys import getsizeof
from song import Song, SongPart
from collections import namedtuple
from threading import Thread, Event
from abc import ABCMeta, abstractmethod
from magenta.music import trim_note_sequence
from magenta.protobuf.music_pb2 import NoteSequence
from magenta.protobuf.generator_pb2 import GeneratorOptions
from magenta.music.testing_lib import add_track_to_sequence

Note = namedtuple('Note', ['pitch', 'velocity', 'start', 'end'])


def adjust_sequence_times(sequence, delta_time):
    retimed_sequence = NoteSequence()
    retimed_sequence.CopyFrom(sequence)
    for note in retimed_sequence.notes:
        note.start_time += delta_time
        note.end_time += delta_time
    retimed_sequence.total_time += delta_time
    return retimed_sequence


def generate_midi_chord(notes, start_time, duration=2, velocity=100):
    return [Note(note, velocity, start_time, duration) for note in notes]


class State(Enum):
    IDLE = 0
    LISTENING = 1
    RESPONDING = 2


class CacheItem():

    def __init__(self, sequence, response_start_time):
        self.sequence = sequence
        self.response_start_time = response_start_time


class MidiInteraction(Thread, metaclass=ABCMeta):
    # _metaclass__ = ABCMeta

    _BASE_QPM = 60  # Base QPM when set by a tempo control change.

    def __init__(self,
                 midi_hub,
                 sequence_generators,
                 qpm,
                 generator_select_control_number=None,
                 tempo_control_number=None,
                 temperature_control_number=None):
        self._midi_hub = midi_hub
        self._sequence_generators = sequence_generators
        self._default_qpm = qpm
        self._generator_select_control_number = generator_select_control_number
        self._tempo_control_number = tempo_control_number
        self._temperature_control_number = temperature_control_number
        self._stop_signal = Event()
        super(MidiInteraction, self).__init__()

    @property
    def _sequence_generator(self):
        if len(self._sequence_generators) == 1:
            return self._sequence_generators[0]
        val = self._midi_hub.control_value(
            self._generator_select_control_number)
        val = 0 if val is None else val
        return self._sequence_generators[val % len(self._sequence_generators)]

    @property
    def _qpm(self):
        val = self._midi_hub.control_value(self._tempo_control_number)
        return self._default_qpm if val is None else val + self._BASE_QPM

    @property
    def _temperature(self, min_temp=0.1, max_temp=2.0, default=1.0):
        val = self._midi_hub.control_value(self._temperature_control_number)
        if val is None:
            return default
        return min_temp + (val / 127.) * (max_temp - min_temp)

    @abstractmethod
    def run(self):
        pass

    def stopped(self):
        return self._stop_signal.is_set()

    def stop(self):
        self._stop_signal.set()
        self.join()


class SongStructureMidiInteraction(MidiInteraction):
    STRUCTURE = []
    MELODY_CACHE = {}
    BASS_CACHE = {}
    DRUM_CACHE = {}

    def __init__(self,
                 midi_hub,
                 sequence_generators,
                 qpm,
                 structure,
                 chord_passthrough=False,
                 generator_select_control_number=None,
                 clock_signal=None,
                 tick_duration=None,
                 end_call_signal=None,
                 panic_signal=None,
                 mutate_signal=None,
                 allow_overlap=False,
                 metronome_channel=None,
                 min_listen_ticks_control_number=None,
                 max_listen_ticks_control_number=None,
                 response_ticks_control_number=None,
                 tempo_control_number=None,
                 temperature_control_number=None,
                 loop_control_number=None,
                 state_control_number=None,
                 ):
        super(SongStructureMidiInteraction, self).__init__(
            midi_hub, sequence_generators, qpm, generator_select_control_number,
            tempo_control_number, temperature_control_number)
        if [clock_signal, tick_duration].count(None) != 1:
            raise ValueError(
                'Exactly one of `clock_signal` or `tick_duration` must be specified.')
        self.STRUCTURE = structure
        self.MELODY_CACHE = {part.name: None for part in self.STRUCTURE}
        self.BASS_CACHE = {part.name: None for part in self.STRUCTURE}
        self.DRUM_CACHE = {part.name: None for part in self.STRUCTURE}
        self._clock_signal = clock_signal
        self._tick_duration = tick_duration
        self._end_call_signal = end_call_signal
        self._panic_signal = panic_signal
        self._mutate_signal = mutate_signal
        self._allow_overlap = allow_overlap
        self._metronome_channel = metronome_channel
        self._min_listen_ticks_control_number = min_listen_ticks_control_number
        self._max_listen_ticks_control_number = max_listen_ticks_control_number
        self._response_ticks_control_number = response_ticks_control_number
        self._loop_control_number = loop_control_number
        self._state_control_number = state_control_number
        self.chord_passthrough = chord_passthrough
        # Event for signalling when to end a call.
        self._end_call = Event()
        # Event for signalling when to flush playback sequence.
        self._panic = Event()
        # Event for signalling when to mutate response.
        self._mutate = Event()

    def _update_state(self, state):
        if self._state_control_number is not None:
            self._midi_hub.send_control_change(
                self._state_control_number, state)
        logging.info('State: %s', state)

    def _end_call_callback(self, unused_captured_seq):
        self._end_call.set()
        logging.info('End call signal received.')

    def _panic_callback(self, unused_captured_seq):
        self._panic.set()
        logging.info('Panic signal received.')

    def _mutate_callback(self, unused_captured_seq):
        self._mutate.set()
        logging.info('Mutate signal received.')

    @property
    def _min_listen_ticks(self):
        val = self._midi_hub.control_value(
            self._min_listen_ticks_control_number)
        return 0 if val is None else val

    @property
    def _max_listen_ticks(self):
        val = self._midi_hub.control_value(
            self._max_listen_ticks_control_number)
        return float('inf') if not val else val

    @property
    def _should_loop(self):
        return (self._loop_control_number and self._midi_hub.control_value(self._loop_control_number) == 127)

    def stop(self):
        self._stop_signal.set()
        self._captor.stop()
        self._midi_hub.stop_metronome()
        super(SongStructureMidiInteraction, self).stop()

    def _generate(self, gen_index, input_sequence, zero_time, response_start_time, response_end_time):
        response_start_time -= zero_time
        response_end_time -= zero_time

        generator_options = GeneratorOptions()
        generator_options.input_sections.add(
            start_time=0,
            end_time=response_start_time)
        generator_options.generate_sections.add(
            start_time=response_start_time,
            end_time=response_end_time)

        # Set current temperature setting.
        generator_options.args['temperature'].float_value = self._temperature

        # Generate response.
        generator = self._sequence_generators[gen_index]
        logging.warn("Generating sequence using '%s' generator.",
                     generator.details.id)
        # logging.warn('Generator Details: %s', generator.details)
        # logging.warn('Bundle Details: %s', generator.bundle_details)
        # logging.warn('Generator Options: %s', generator_options)
        response_sequence = generator.generate(adjust_sequence_times(
            input_sequence, -zero_time), generator_options)
        response_sequence = trim_note_sequence(
            response_sequence, response_start_time, response_end_time)
        return adjust_sequence_times(response_sequence, zero_time)

    def run(self):
        start_time = time()
        self._captor = self._midi_hub.start_capture(self._qpm, start_time)

        if not self._clock_signal and self._metronome_channel is not None:
            self._midi_hub.start_metronome(
                self._qpm, start_time, channel=self._metronome_channel)

        # Register callbacks
        if self._end_call_signal is not None:
            self._captor.register_callback(
                self._end_call_callback, signal=self._end_call_signal)
        if self._panic_signal is not None:
            self._captor.register_callback(
                self._panic_callback, signal=self._panic_signal)
        if self._mutate_signal is not None:
            self._captor.register_callback(
                self._mutate_callback, signal=self._mutate_signal)

        # Keep track of the end of the previous tick time.
        last_tick_time = time()

        # Keep track of the duration of a listen state.
        listen_ticks = 0

        # Start with an empty response sequence.
        response_sequence = NoteSequence()
        response_start_time = 0
        response_duration = 0
        player_melody = self._midi_hub.start_playback(
            response_sequence, playback_channel=1, allow_updates=True)
        player_bass = self._midi_hub.start_playback(
            response_sequence, playback_channel=2, allow_updates=True)
        player_chords = self._midi_hub.start_playback(
            response_sequence, playback_channel=3, allow_updates=True)
        player_drums = self._midi_hub.start_playback(
            response_sequence, playback_channel=9, allow_updates=True)

        # Song structure data
        part_in_song = 0  # index to STRUCTURE list
        bars_played = 0  # absolute number of bars played
        part_duration = 8

        # Enter loop at each clock tick.
        for captured_sequence in self._captor.iterate(signal=self._clock_signal, period=self._tick_duration):
            if self._stop_signal.is_set():
                break
            if self._panic.is_set():
                response_sequence = NoteSequence()
                player_melody.update_sequence(response_sequence)
                player_bass.update_sequence(response_sequence)
                player_chords.update_sequence(response_sequence)
                player_drums.update_sequence(response_sequence)
                self._panic.clear()

            tick_time = captured_sequence.total_time

            # Set to current QPM, since it might have changed.
            if not self._clock_signal and self._metronome_channel is not None:
                self._midi_hub.start_metronome(
                    self._qpm, tick_time, channel=self._metronome_channel)
            captured_sequence.tempos[0].qpm = self._qpm

            tick_duration = tick_time - last_tick_time
            last_end_time = (max(
                note.end_time for note in captured_sequence.notes) if captured_sequence.notes else 0.0)

            # True if there was no input captured during the last tick.
            silent_tick = last_end_time <= last_tick_time

            if not silent_tick:
                listen_ticks += 1

            part_in_song = bars_played // part_duration
            bar_in_part = bars_played % part_duration
            if part_in_song >= len(self.STRUCTURE):
                break
            # print("{} [BAR {}]".format(self.STRUCTURE[part_in_song], bar_in_part))
            response_duration = part_duration * tick_duration
            response_start_time = tick_time
            capture_start_time = self._captor.start_time
            if silent_tick:  # Move the sequence forward one tick in time.
                captured_sequence = adjust_sequence_times(
                    captured_sequence, tick_duration)
                captured_sequence.total_time = tick_time
                capture_start_time += tick_duration

            if bars_played % part_duration == 0:
                part = self.STRUCTURE[part_in_song]

                if self.MELODY_CACHE[part.name]:
                    melody_sequence = self.MELODY_CACHE[part.name].sequence
                    response_start_time = self.MELODY_CACHE[
                        part.name].response_start_time
                else:
                    logging.info("new melody sequence")
                    melody_sequence = self._generate(
                        0,
                        captured_sequence,
                        capture_start_time,
                        response_start_time,
                        response_start_time + response_duration)
                    self.MELODY_CACHE[part.name] = CacheItem(
                        melody_sequence, capture_start_time)

                if self.BASS_CACHE[part.name]:
                    bass_sequence = self.BASS_CACHE[part.name].sequence
                    response_start_time = self.BASS_CACHE[
                        part.name].response_start_time
                else:
                    logging.info("new bass sequence")
                    bass_sequence = self._generate(
                        1,
                        captured_sequence,
                        capture_start_time,
                        response_start_time,
                        response_start_time + response_duration)
                    self.BASS_CACHE[part.name] = CacheItem(
                        bass_sequence, capture_start_time)

                if self.DRUM_CACHE[part.name]:
                    drum_sequence = self.DRUM_CACHE[part.name].sequence
                    response_start_time = self.DRUM_CACHE[
                        part.name].response_start_time
                else:
                    logging.info("new drum sequence")
                    drum_sequence = self._generate(
                        2,
                        captured_sequence,
                        capture_start_time,
                        response_start_time,
                        response_start_time + response_duration)
                    self.DRUM_CACHE[part.name] = CacheItem(
                        drum_sequence, capture_start_time)

                size = getsizeof(self.MELODY_CACHE)
                size += getsizeof(self.BASS_CACHE)
                size += getsizeof(self.DRUM_CACHE)
                logging.info(f"Cache Size: {size // 8}KB")

                chord_sequence = NoteSequence()
                notes = []
                chords = part.get_midi_chords()
                chords += chords
                for i, chord in enumerate(chords):
                    for note in generate_midi_chord(chord, 2 * i, 2):
                        notes.append(note)
                notes = [Note(note.pitch, note.velocity, note.start + response_start_time,
                              note.start + note.end + response_start_time) for note in notes]
                add_track_to_sequence(chord_sequence, 0, notes)

                # If it took too long to generate, push response to next tick.
                if (time() - response_start_time) >= tick_duration / 4:
                    push_ticks = (
                        (time() - response_start_time) // tick_duration + 1)
                    response_start_time += push_ticks * tick_duration
                    melody_sequence = adjust_sequence_times(
                        melody_sequence, push_ticks * tick_duration)
                    bass_sequence = adjust_sequence_times(
                        bass_sequence, push_ticks * tick_duration)
                    chord_sequence = adjust_sequence_times(
                        chord_sequence, push_ticks * tick_duration)
                    drum_sequence = adjust_sequence_times(
                        drum_sequence, push_ticks * tick_duration)
                    self.MELODY_CACHE[
                        part.name].response_start_time = response_start_time
                    self.BASS_CACHE[
                        part.name].response_start_time = response_start_time
                    self.DRUM_CACHE[
                        part.name].response_start_time = response_start_time
                    logging.warn(
                        'Response too late. Pushing back %d ticks.', push_ticks)

                # Start response playback. Specify the start_time to avoid stripping initial events due to generation lag.
                # Before playback:
                # - transpose melody and bass for MelodicFlow
                # - map notes that lie on black keys to a white key to prevent triggering MelodicFlow
                player_melody.update_sequence(
                    melody_sequence, start_time=response_start_time)
                player_bass.update_sequence(
                    bass_sequence, start_time=response_start_time)
                if self.chord_passthrough:
                    player_chords.update_sequence(
                        chord_sequence, start_time=response_start_time)
                player_drums.update_sequence(
                    drum_sequence, start_time=response_start_time)

            if not captured_sequence.notes:
                # Reset captured sequence since we are still idling.
                if melody_sequence.total_time <= tick_time:
                    self._update_state(State.IDLE)
                if self._captor.start_time < tick_time:
                    self._captor.start_time = tick_time
                # we hit this thing when we're playing generated stuff, but
                # neither of the above if's are activated
                self._end_call.clear()
                listen_ticks = 0
            elif (self._end_call.is_set() or silent_tick or listen_ticks >= self._max_listen_ticks):
                pass
            else:
                self._update_state(State.LISTENING)  # Continue listening.

            last_tick_time = tick_time
            bars_played += 1

        player_melody.stop()
        player_bass.stop()
        player_chords.stop()
        player_drums.stop()
