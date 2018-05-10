from mingus.containers import NoteContainer
from mingus.core.progressions import to_chords
from mingus.core.keys import Key


class SongPart(list):

    def __init__(self, name, chords=[]):
        super(SongPart, self).__init__(chords)
        self.name = name

    def duration(self):
        # one chord is one bar in length for the moment
        # so a part with 4 chords has a length of 4 bars
        return len(self)

    def get_midi_chords(self, key="C", shift=0):
        return [[int(note) + shift for note in NoteContainer(chord)] for chord in to_chords(self, key)]

    def __repr__(self):
        return f"SongPart(name={self.name}, chords={super(SongPart, self).__repr__()})"


class Song(list):

    def __init__(self, parts=[]):
        super(Song, self).__init__(parts)

    def duration(self):
        return sum((part.duration() for part in self))
