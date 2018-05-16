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

### System ###
import re
import abc
import time
import logging
import threading
from queue import Queue
from collections import deque
from collections import defaultdict

### Mido ###
import mido

### Tensorflow ###
import tensorflow as tf

### Magenta ###
from magenta.common import concurrency
from magenta.protobuf import music_pb2

_DEFAULT_METRONOME_TICK_DURATION = 0.05
_DEFAULT_METRONOME_PROGRAM = 117  # Melodic Tom
_DEFAULT_METRONOME_MESSAGES = [
    mido.Message(type='note_on', note=44, velocity=64),
    mido.Message(type='note_on', note=35, velocity=64),
    mido.Message(type='note_on', note=35, velocity=64),
    mido.Message(type='note_on', note=35, velocity=64),
]
_DEFAULT_METRONOME_CHANNEL = 1

# 0-indexed
_DRUM_CHANNEL = 9


class MidiHubException(Exception):
    pass


class TextureType(object):
    MONOPHONIC = 1
    POLYPHONIC = 2


class MidiSignal(object):
    _NOTE_ARGS = set(['type', 'note', 'program_number', 'velocity'])
    _CONTROL_ARGS = set(['type', 'control', 'value'])
    _VALID_ARGS = {
        'note_on': _NOTE_ARGS,
        'note_off': _NOTE_ARGS,
        'control_change': _CONTROL_ARGS,
    }

    def __init__(self, msg=None, **kwargs):
        if msg is not None and kwargs:
            raise MidiHubException(
                'Either a mido.Message should be provided or arguments. Not both.')

        type_ = msg.type if msg is not None else kwargs.get('type')
        if 'type' in kwargs:
            del kwargs['type']

        if type_ is not None and type_ not in self._VALID_ARGS:
            raise MidiHubException(
                "The type of a MidiSignal must be either 'note_on', 'note_off', "
                "'control_change' or None for wildcard matching. Got '%s'." % type_)

        # The compatible mido.Message types.
        inferred_types = [type_] if type_ is not None else []
        # If msg is not provided, check that the given arguments are valid for some
        # message type.
        if msg is None:
            if type_ is not None:
                for arg_name in kwargs:
                    if arg_name not in self._VALID_ARGS[type_]:
                        raise MidiHubException(
                            "Invalid argument for type '%s': %s" % (type_, arg_name))
            else:
                if kwargs:
                    for name, args in self._VALID_ARGS.items():
                        if set(kwargs) <= args:
                            inferred_types.append(name)
                if not inferred_types:
                    raise MidiHubException(
                        'Could not infer a message type for set of given arguments: %s'
                        % ', '.join(kwargs))
                # If there is only a single valid inferred type, use it.
                if len(inferred_types) == 1:
                    type_ = inferred_types[0]

        self._msg = msg
        self._kwargs = kwargs
        self._type = type_
        self._inferred_types = inferred_types

    def to_message(self):
        if self._msg:
            return self._msg
        if not self._type:
            raise MidiHubException(
                'Cannot build message if type is not inferrable.')
        return mido.Message(self._type, **self._kwargs)

    def __str__(self):
        if self._msg is not None:
            regex_pattern = '^' + mido.messages.format_as_string(
                self._msg, include_time=False) + r' time=\d+.\d+$'
        else:
            # Generate regex pattern.
            parts = ['.*' if self._type is None else self._type]
            for name in mido.messages.SPEC_BY_TYPE[self._inferred_types[0]][
                    'value_names']:
                if name in self._kwargs:
                    parts.append('%s=%d' % (name, self._kwargs[name]))
                else:
                    parts.append(r'%s=\d+' % name)
            regex_pattern = '^' + ' '.join(parts) + r' time=\d+.\d+$'
        return regex_pattern


class Metronome(threading.Thread):
    daemon = True

    def __init__(self, outport, qpm, start_time, stop_time=None, program=_DEFAULT_METRONOME_PROGRAM, signals=None, duration=_DEFAULT_METRONOME_TICK_DURATION, channel=None):
        self._outport = outport
        self.update(qpm, start_time, stop_time,
                    program, signals, duration, channel)
        super(Metronome, self).__init__()

    def update(self, qpm, start_time, stop_time=None, program=_DEFAULT_METRONOME_PROGRAM, signals=None, duration=_DEFAULT_METRONOME_TICK_DURATION, channel=None):
        # Locking is not required since variables are independent and assignment is
        # atomic.
        self._channel = _DEFAULT_METRONOME_CHANNEL if channel is None else channel

        # Set the program number for the channels.
        self._outport.send(
            mido.Message(
                type='program_change', program=program, channel=self._channel))
        self._period = 60. / qpm
        self._start_time = start_time
        self._stop_time = stop_time
        self._messages = (_DEFAULT_METRONOME_MESSAGES if signals is None else
                          [s.to_message() if s else None for s in signals])
        self._duration = duration

    def run(self):
        sleeper = concurrency.Sleeper()
        while True:
            now = time.time()
            tick_number = max(
                0, int((now - self._start_time) // self._period) + 1)
            tick_time = tick_number * self._period + self._start_time

            if self._stop_time is not None and self._stop_time < tick_time:
                break

            sleeper.sleep_until(tick_time)

            metric_position = tick_number % len(self._messages)
            tick_message = self._messages[metric_position]

            if tick_message is None:
                continue

            tick_message.channel = self._channel
            self._outport.send(tick_message)

            if tick_message.type == 'note_on':
                sleeper.sleep(self._duration)
                end_tick_message = mido.Message(
                    'note_off', note=tick_message.note, channel=self._channel)
                self._outport.send(end_tick_message)

    def stop(self, stop_time=0, block=True):
        self._stop_time = stop_time
        if block:
            self.join()


class MidiPlayer(threading.Thread):

    def __init__(self, outport, sequence, start_time=time.time(),
                 allow_updates=False, channel=0, offset=0.0):
        self._outport = outport
        self._channel = channel
        self._offset = offset

        # Set of notes (pitches) that are currently on.
        self._open_notes = set()
        # Lock for serialization.
        self._lock = threading.RLock()
        # A control variable to signal when the sequence has been updated.
        self._update_cv = threading.Condition(self._lock)
        # The queue of mido.Message objects to send, sorted by ascending time.
        self._message_queue = deque()
        # An event that is set when `stop` has been called.
        self._stop_signal = threading.Event()

        # Initialize message queue.
        # We first have to allow "updates" to set the initial sequence.
        self._allow_updates = True
        self.update_sequence(sequence, start_time=start_time)
        # We now make whether we allow updates dependent on the argument.
        self._allow_updates = allow_updates

        super(MidiPlayer, self).__init__()

    @concurrency.serialized
    def update_sequence(self, sequence, start_time=None):
        if start_time is None:
            start_time = time.time()

        if not self._allow_updates:
            raise MidiHubException(
                'Attempted to update a MidiPlayer sequence with updates disabled.')

        new_message_list = []
        # The set of pitches that are already playing and will be closed without
        # first being reopened in in the new sequence.
        closed_notes = set()
        for note in sequence.notes:
            if note.start_time >= start_time:
                new_message_list.append(
                    mido.Message(type='note_on', note=note.pitch,
                                 velocity=note.velocity, time=note.start_time))
                new_message_list.append(
                    mido.Message(type='note_off', note=note.pitch, time=note.end_time))
            elif note.end_time >= start_time and note.pitch in self._open_notes:
                new_message_list.append(
                    mido.Message(type='note_off', note=note.pitch, time=note.end_time))
                closed_notes.add(note.pitch)

        # Close remaining open notes at the next event time to avoid abruptly ending
        # notes.
        notes_to_close = self._open_notes - closed_notes
        if notes_to_close:
            next_event_time = (
                min(msg.time for msg in new_message_list) if new_message_list else 0)
            for note in notes_to_close:
                new_message_list.append(
                    mido.Message(type='note_off', note=note, time=next_event_time))

        for msg in new_message_list:
            msg.channel = self._channel
            msg.time += self._offset

        self._message_queue = deque(
            sorted(new_message_list, key=lambda msg: (msg.time, msg.note)))
        self._update_cv.notify()

    @concurrency.serialized
    def run(self):
        while self._message_queue and self._message_queue[0].time < time.time():
            self._message_queue.popleft()

        while True:
            while self._message_queue:
                delta = self._message_queue[0].time - time.time()
                if delta > 0:
                    self._update_cv.wait(timeout=delta)
                else:
                    msg = self._message_queue.popleft()
                    if msg.type == 'note_on':
                        self._open_notes.add(msg.note)
                    elif msg.type == 'note_off':
                        self._open_notes.discard(msg.note)
                    self._outport.send(msg)

            # Either keep player alive and wait for sequence update, or return.
            if self._allow_updates:
                self._update_cv.wait()
            else:
                break

    def stop(self, block=True):
        with self._lock:
            if not self._stop_signal.is_set():
                self._stop_signal.set()
                self._allow_updates = False

                # Replace message queue with immediate end of open notes.
                self._message_queue.clear()
                for note in self._open_notes:
                    self._message_queue.append(
                        mido.Message(type='note_off', note=note, time=time.time()))
                self._update_cv.notify()
        if block:
            self.join()


class MidiCaptor(threading.Thread):
    _metaclass__ = abc.ABCMeta

    # A message that is used to wake the consumer thread.
    _WAKE_MESSAGE = None

    def __init__(self, qpm, start_time=0, stop_time=None, stop_signal=None):
                # A lock for synchronization.
        self._lock = threading.RLock()
        self._receive_queue = Queue()
        self._captured_sequence = music_pb2.NoteSequence()
        self._captured_sequence.tempos.add(qpm=qpm)
        self._start_time = start_time
        self._stop_time = stop_time
        self._stop_regex = re.compile(str(stop_signal))
        # A set of active MidiSignals being used by iterators.
        self._iter_signals = []
        # An event that is set when `stop` has been called.
        self._stop_signal = threading.Event()
        # Active callback threads keyed by unique thread name.
        self._callbacks = {}
        super(MidiCaptor, self).__init__()

    @property
    @concurrency.serialized
    def start_time(self):
        return self._start_time

    @start_time.setter
    @concurrency.serialized
    def start_time(self, value):
        self._start_time = value
        i = 0
        for note in self._captured_sequence.notes:
            if note.start_time >= self._start_time:
                break
            i += 1
        del self._captured_sequence.notes[:i]

    @property
    @concurrency.serialized
    def _stop_time(self):
        return self._stop_time_unsafe

    @_stop_time.setter
    @concurrency.serialized
    def _stop_time(self, value):
        self._stop_time_unsafe = value

    def receive(self, msg):
        if not msg.time:
            raise MidiHubException(
                'MidiCaptor received message with empty time attribute: %s' % msg)
        self._receive_queue.put(msg)

    @abc.abstractmethod
    def _capture_message(self, msg):
        pass

    def _add_note(self, msg):
        """Adds and returns a new open note based on the MIDI message."""
        new_note = self._captured_sequence.notes.add()
        new_note.start_time = msg.time
        new_note.pitch = msg.note
        new_note.velocity = msg.velocity
        new_note.is_drum = (msg.channel == _DRUM_CHANNEL)
        return new_note

    def run(self):
        """Captures incoming messages until stop time or signal received."""
        while True:
            timeout = None
            stop_time = self._stop_time
            if stop_time is not None:
                timeout = stop_time - time.time()
                if timeout <= 0:
                    break
            try:
                msg = self._receive_queue.get(block=True, timeout=timeout)
            except Queue.Empty:
                continue

            if msg is MidiCaptor._WAKE_MESSAGE:
                continue

            if msg.time <= self._start_time:
                continue

            if self._stop_regex.match(str(msg)) is not None:
                break

            with self._lock:
                msg_str = str(msg)
                for regex, queue in self._iter_signals:
                    if regex.match(msg_str) is not None:
                        queue.put(msg.copy())

            self._capture_message(msg)

        stop_time = self._stop_time
        end_time = stop_time if stop_time is not None else msg.time

        # Acquire lock to avoid race condition with `iterate`.
        with self._lock:
            # Set final captured sequence.
            self._captured_sequence = self.captured_sequence(end_time)
            # Wake up all generators.
            for regex, queue in self._iter_signals:
                queue.put(MidiCaptor._WAKE_MESSAGE)

    def stop(self, stop_time=None, block=True):
        with self._lock:
            if self._stop_signal.is_set():
                if stop_time is not None:
                    raise MidiHubException(
                        '`stop` must not be called multiple times with a `stop_time` on '
                        'MidiCaptor.')
            else:
                self._stop_signal.set()
                self._stop_time = time.time() if stop_time is None else stop_time
                # Force the thread to wake since we've updated the stop time.
                self._receive_queue.put(MidiCaptor._WAKE_MESSAGE)
        if block:
            self.join()

    def captured_sequence(self, end_time=None):
        # Make a copy of the sequence currently being captured.
        current_captured_sequence = music_pb2.NoteSequence()
        with self._lock:
            current_captured_sequence.CopyFrom(self._captured_sequence)

        if self.is_alive():
            if end_time is None:
                raise MidiHubException(
                    '`end_time` must be provided when capture thread is still running.')
            for i, note in enumerate(current_captured_sequence.notes):
                if note.start_time >= end_time:
                    del current_captured_sequence.notes[i:]
                    break
                if not note.end_time or note.end_time > end_time:
                    note.end_time = end_time
            current_captured_sequence.total_time = end_time
        elif end_time is not None:
            raise MidiHubException(
                '`end_time` must not be provided when capture is complete.')

        return current_captured_sequence

    def iterate(self, signal=None, period=None):
        if (signal, period).count(None) != 1:
            raise MidiHubException(
                'Exactly one of `signal` or `period` must be provided to `iterate` '
                'call.')

        if signal is None:
            sleeper = concurrency.Sleeper()
            next_yield_time = time.time() + period
        else:
            regex = re.compile(str(signal))
            queue = Queue()
            with self._lock:
                self._iter_signals.append((regex, queue))

        while self.is_alive():
            if signal is None:
                skipped_periods = (time.time() - next_yield_time) // period
                if skipped_periods > 0:
                    tf.logging.warn(
                        'Skipping %d %.3fs period(s) to catch up on iteration.',
                        skipped_periods, period)
                    next_yield_time += skipped_periods * period
                else:
                    sleeper.sleep_until(next_yield_time)
                end_time = next_yield_time
                next_yield_time += period
            else:
                signal_msg = queue.get()
                if signal_msg is MidiCaptor._WAKE_MESSAGE:
                    # This is only recieved when the thread is in the process of
                    # terminating. Wait until it is done before yielding the final
                    # sequence.
                    self.join()
                    break
                end_time = signal_msg.time
            # Acquire lock so that `captured_sequence` will be called before thread
            # terminates, if it has not already done so.
            with self._lock:
                if not self.is_alive():
                    break
                captured_sequence = self.captured_sequence(end_time)
            yield captured_sequence
        yield self.captured_sequence()

    def register_callback(self, fn, signal=None, period=None):
        class IteratorCallback(threading.Thread):
            """A thread for executing a callback on each iteration."""

            def __init__(self, iterator, fn):
                self._iterator = iterator
                self._fn = fn
                self._stop_signal = threading.Event()
                super(IteratorCallback, self).__init__()

            def run(self):
                """Calls the callback function for each iterator value."""
                for captured_sequence in self._iterator:
                    if self._stop_signal.is_set():
                        break
                    self._fn(captured_sequence)

            def stop(self):
                """Stops the thread on next iteration, without blocking."""
                self._stop_signal.set()

        t = IteratorCallback(self.iterate(signal, period), fn)
        t.start()

        with self._lock:
            assert t.name not in self._callbacks
            self._callbacks[t.name] = t

        return t.name

    @concurrency.serialized
    def cancel_callback(self, name):
        self._callbacks[name].stop()
        del self._callbacks[name]


class MonophonicMidiCaptor(MidiCaptor):

    def __init__(self, *args, **kwargs):
        self._open_note = None
        super(MonophonicMidiCaptor, self).__init__(*args, **kwargs)

    @concurrency.serialized
    def _capture_message(self, msg):
        if msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            if self._open_note is None or msg.note != self._open_note.pitch:
                # This is not the note we're looking for. Drop it.
                return

            self._open_note.end_time = msg.time
            self._open_note = None

        elif msg.type == 'note_on':
            if self._open_note:
                if self._open_note.pitch == msg.note:
                    # This is just a repeat of the previous message.
                    return
                # End the previous note.
                self._open_note.end_time = msg.time

            self._open_note = self._add_note(msg)


class PolyphonicMidiCaptor(MidiCaptor):

    def __init__(self, *args, **kwargs):
        self._open_notes = dict()
        super(PolyphonicMidiCaptor, self).__init__(*args, **kwargs)

    @concurrency.serialized
    def _capture_message(self, msg):
        if msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            if msg.note not in self._open_notes:
                # This is not a note we're looking for. Drop it.
                return

            self._open_notes[msg.note].end_time = msg.time
            del self._open_notes[msg.note]

        elif msg.type == 'note_on':
            if msg.note in self._open_notes:
                # This is likely just a repeat of the previous message.
                return

            new_note = self._add_note(msg)
            self._open_notes[new_note.pitch] = new_note


class MidiHub(object):

    def __init__(self, input_midi_ports, output_midi_ports, texture_type, passthrough=True, playback_offset=0.0):
        self._texture_type = texture_type
        self._passthrough = passthrough
        self._playback_offset = playback_offset
        # When `passthrough` is True, this is the set of open MIDI note
        # pitches.
        self._open_notes = set()
        # This lock is used by the serialized decorator.
        self._lock = threading.RLock()
        # A dictionary mapping a compiled MidiSignal regex to a condition variable
        # that will be notified when a matching messsage is received.
        self._signals = {}
        # A dictionary mapping a compiled MidiSignal regex to a list of functions
        # that will be called with the triggering message in individual threads when
        # a matching message is received.
        self._callbacks = defaultdict(list)
        # A dictionary mapping integer control numbers to most recently-received
        # integer value.
        self._control_values = {}
        # Threads actively being used to capture incoming messages.
        self._captors = []
        # Potentially active player threads.
        self._players = []
        self._metronome = None

        # Open MIDI ports.

        if input_midi_ports:
            for port in input_midi_ports:
                if isinstance(port, mido.ports.BaseInput):
                    inport = port
                else:
                    virtual = port not in mido.get_input_names()
                    if virtual:
                        logging.info(
                            "Opening '%s' as a virtual MIDI port for input.", port)
                    inport = mido.open_input(port, virtual=virtual)
                # Start processing incoming messages.
                inport.callback = self._timestamp_and_handle_message
                # TODO: this is needed because otherwise inport will get
                # garbage collected and stop receiving input events
                self._inport = inport
        else:
            logging.warn('No input port specified. Capture disabled.')
            self._inport = None

        outports = []
        for port in output_midi_ports:
            if isinstance(port, mido.ports.BaseInput):
                outports.append(port)
            else:
                virtual = port not in mido.get_output_names()
                if virtual:
                    logging.info(
                        "Opening '%s' as a virtual MIDI port for output.", port)
                outports.append(mido.open_output(port, virtual=virtual))
        self._outport = mido.ports.MultiPort(outports)

    def __del__(self):
        for captor in self._captors:
            captor.stop(block=False)
        for player in self._players:
            player.stop(block=False)
        self.stop_metronome()
        for captor in self._captors:
            captor.join()
        for player in self._players:
            player.join()

    @property
    @concurrency.serialized
    def passthrough(self):
        return self._passthrough

    @passthrough.setter
    @concurrency.serialized
    def passthrough(self, value):
        if self._passthrough == value:
            return
        # Close all open notes.
        while self._open_notes:
            self._outport.send(mido.Message(
                'note_off', note=self._open_notes.pop()))
        self._passthrough = value

    def _timestamp_and_handle_message(self, msg):
        if msg.type == 'program_change':
            return
        if not msg.time:
            msg.time = time.time()
        self._handle_message(msg)

    @concurrency.serialized
    def _handle_message(self, msg):
        # Notify any threads waiting for this message.
        msg_str = str(msg)
        for regex in list(self._signals):
            if regex.match(msg_str) is not None:
                self._signals[regex].notify_all()
                del self._signals[regex]

        # Call any callbacks waiting for this message.
        for regex in list(self._callbacks):
            if regex.match(msg_str) is not None:
                for fn in self._callbacks[regex]:
                    threading.Thread(target=fn, args=(msg,)).start()

                del self._callbacks[regex]

        # Remove any captors that are no longer alive.
        self._captors[:] = [t for t in self._captors if t.is_alive()]
        # Add a different copy of the message to the receive queue of each live
        # capture thread.
        for t in self._captors:
            t.receive(msg.copy())

        # Update control values if this is a control change message.
        if msg.type == 'control_change':
            if self._control_values.get(msg.control, None) != msg.value:
                logging.debug('Control change %d: %d',
                              msg.control, msg.value)
            self._control_values[msg.control] = msg.value

        # Pass the message through to the output port, if appropriate.
        if not self._passthrough:
            pass
        elif self._texture_type == TextureType.POLYPHONIC:
            if msg.type == 'note_on' and msg.velocity > 0:
                self._open_notes.add(msg.note)
            elif (msg.type == 'note_off' or
                  (msg.type == 'note_on' and msg.velocity == 0)):
                self._open_notes.discard(msg.note)
            self._outport.send(msg)
        elif self._texture_type == TextureType.MONOPHONIC:
            assert len(self._open_notes) <= 1
            if msg.type not in ['note_on', 'note_off']:
                self._outport.send(msg)
            elif ((msg.type == 'note_off' or
                   msg.type == 'note_on' and msg.velocity == 0) and
                  msg.note in self._open_notes):
                self._outport.send(msg)
                self._open_notes.remove(msg.note)
            elif msg.type == 'note_on' and msg.velocity > 0:
                if self._open_notes:
                    self._outport.send(
                        mido.Message('note_off', note=self._open_notes.pop()))
                self._outport.send(msg)
                self._open_notes.add(msg.note)

    def start_capture(self, qpm, start_time, stop_time=None, stop_signal=None):
        captor_class = (MonophonicMidiCaptor if
                        self._texture_type == TextureType.MONOPHONIC else
                        PolyphonicMidiCaptor)
        captor = captor_class(qpm, start_time, stop_time, stop_signal)
        with self._lock:
            self._captors.append(captor)
        captor.start()
        return captor

    def capture_sequence(self, qpm, start_time, stop_time=None, stop_signal=None):
        if stop_time is None and stop_signal is None:
            raise MidiHubException(
                'At least one of `stop_time` and `stop_signal` must be provided to '
                '`capture_sequence` call.')
        captor = self.start_capture(qpm, start_time, stop_time, stop_signal)
        captor.join()
        return captor.captured_sequence()

    @concurrency.serialized
    def wait_for_event(self, signal=None, timeout=None):
        if (signal, timeout).count(None) != 1:
            raise MidiHubException(
                'Exactly one of `signal` or `timeout` must be provided to '
                '`wait_for_event` call.')

        if signal is None:
            concurrency.Sleeper().sleep(timeout)
            return

        signal_pattern = str(signal)
        cond_var = None
        for regex, cond_var in self._signals:
            if regex.pattern == signal_pattern:
                break
        if cond_var is None:
            cond_var = threading.Condition(self._lock)
            self._signals[re.compile(signal_pattern)] = cond_var

        cond_var.wait()

    @concurrency.serialized
    def wake_signal_waiters(self, signal=None):
        for regex in list(self._signals):
            if signal is None or regex.pattern == str(signal):
                self._signals[regex].notify_all()
                del self._signals[regex]
        for captor in self._captors:
            captor.wake_signal_waiters(signal)

    @concurrency.serialized
    def start_metronome(self, qpm, start_time, signals=None, channel=None):
        if self._metronome is not None and self._metronome.is_alive():
            self._metronome.update(
                qpm, start_time, signals=signals, channel=channel)
        else:
            self._metronome = Metronome(
                self._outport, qpm, start_time, signals=signals, channel=channel)
            self._metronome.start()

    @concurrency.serialized
    def stop_metronome(self, stop_time=0, block=True):
        if self._metronome is None:
            return
        self._metronome.stop(stop_time, block)
        self._metronome = None

    def start_playback(self, sequence, playback_channel=0, start_time=time.time(), allow_updates=False):
        player = MidiPlayer(self._outport, sequence, start_time,
                            allow_updates, playback_channel, self._playback_offset)
        with self._lock:
            self._players.append(player)
        player.start()
        return player

    @concurrency.serialized
    def control_value(self, control_number):
        if control_number is None:
            return None
        return self._control_values.get(control_number)

    def send_control_change(self, control_number, value):
        self._outport.send(
            mido.Message(
                type='control_change',
                control=control_number,
                value=value))

    @concurrency.serialized
    def register_callback(self, fn, signal):
        self._callbacks[re.compile(str(signal))].append(fn)
