from enum import Enum
from time import sleep
from threading import Thread, Event
from mido import open_input, open_output, get_input_names, get_output_names
from .midi_meta import white_keys, black_keys, black_keys_flattened, MidiState

delimiter_map = sorted([e[2] for e in white_keys] + [e[5] for e in white_keys])


class KeyType(Enum):
    BLACK = 0
    WHITE = 1


class KeyState(Enum):
    INACTIVE = 0
    ACTIVE = 1


class Delimiter():

    def draw(self):
        return "|||||"


class Key():

    def __init__(self, num):
        self.num = num
        self.type = KeyType.BLACK if num in black_keys_flattened else KeyType.WHITE
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


class Keyboard():

    def __init__(self, channel=0, note_shift=0):
        self.keys = []
        for i in range(127):
            self.keys.append(Key(i))
        self.midi_state = MidiState()
        self.channel = channel
        self.note_shift = -note_shift

    def keyboard_header(self, width):
        part = width // 10
        small_part = width // 20

        left_dots = part
        left_os = small_part
        right_dots = part
        right_os = small_part

        extra_width = width - (part * 2) - (small_part * 4) - 26
        return [" {} ".format("_" * (width - 2)),
                "|{} {}. |..... . .. . |{} [{:2d}] {}{}|".format(":" * left_dots, "o " * left_os, " " * extra_width, self.channel, "o " * right_os, ":" * right_dots),
                "|{} {}  | ..  . ..... |{}      {}{}|".format(":" * left_dots, "o " * left_os, " " * extra_width, "o " * right_os, ":" * right_dots),
                "|{}_{}__|__..._...__._|{}______{}{}|".format(":" * left_dots, "__" * left_os, "_" * extra_width, "__" * right_os, ":" * right_dots)]

    def draw(self, width=57):
        chars = []
        size = 2
        delim = Delimiter()
        active_notes = self.midi_state.active_notes(self.channel)
        chars.append(delim.draw())

        # Calculate how many keys can be shown on screen.
        # If too few keys are available, instantiate more
        # until the screen is filled.
        end = width - len(set(range(width)) & set(delimiter_map)) + 1
        len_before = len(self.keys)
        if len_before < end:
            for i in range(end - len_before):
                self.keys.append(Key(i + len_before))

        for key in self.keys[0:end]:
            if key > 1 and key - 1 in delimiter_map:
                size += 1
                chars.append(delim.draw())
            size += 1
            if key + self.note_shift in active_notes:
                key.activate()
            elif key.active():
                key.deactivate()
            chars.append(key.draw())
        chars.append(delim.draw())
        chars = chars[:width]

        output = self.keyboard_header(width)
        for i in range(len(chars[0])):
            output.append("".join([chars[j][i] for j in range(len(chars))]))

        return output

    def handle_message(self, msg):
        self.midi_state.handle_message(msg)


class MidiPiano(Thread):

    def __init__(self, port_in_name, port_out_name, callback=None):
        super(MidiPiano, self).__init__()
        self.port_in_name = port_in_name
        self.port_out_name = port_out_name
        self.callback = callback
        self.keyboard = Keyboard()
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
