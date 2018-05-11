from mingus.containers import NoteContainer
from mingus.core.progressions import to_chords
from mingus.core.keys import Key


class SongPart(list):

    def __init__(self, name, chords=[]):
        super(SongPart, self).__init__(chords)
        self.name = name

    def duration(self, bars=False):
        beats_per_bar = 4
        bpm = 120
        if bars:
            return len(self)
        return ((len(self) * beats_per_bar) / bpm) * 60

    def get_midi_chords(self, key="C", shift=0):
        return [[int(note) + shift for note in NoteContainer(chord)] for chord in to_chords(self, key)]

    def __repr__(self):
        return f"SongPart(name='{self.name}', chords={super(SongPart, self).__repr__()})"


class Song(list):

    def __init__(self, parts=[]):
        super(Song, self).__init__(parts)

    def duration(self, bars=False):
        return sum((part.duration(bars) for part in self))

    def __repr__(self):
        return f"Song(parts={super(Song, self).__repr__()})"
