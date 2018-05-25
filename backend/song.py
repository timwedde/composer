"""
Classes representing a song on a high level.
"""

### Local ###
from mingus.containers import NoteContainer
from mingus.core.progressions import to_chords


class SongPart(list):
    """A segment in a song with a name and a chord progression."""

    def __init__(self, name, chords=None):
        if not chords:
            chords = []
        super(SongPart, self).__init__(chords)
        self.name = name

    def duration(self, bars=False):
        """
        Returns the duration in seconds that this part will take.
        If 'bars' is True, returns the length in bars instead.
        """
        beats_per_bar = 4
        bpm = 120
        if bars:
            return len(self)
        return ((len(self) * beats_per_bar) / bpm) * 60

    def get_midi_chords(self, key="C", shift=0):
        """Returns a list of NoteContainer() objects that represent each chord in the progression."""
        return [[int(note) + shift for note in NoteContainer(chord)] for chord in to_chords(self, key)]

    def __repr__(self):
        return "SongPart(name='{}', chords={})".format(self.name, super(SongPart, self).__repr__())


class Song(list):
    """A container for a list of SongPart()s, which forms a full song."""

    def __init__(self, parts=None):
        if not parts:
            parts = []
        super(Song, self).__init__(parts)

    def duration(self, bars=False):
        """Returns the summed duration of all SongPart()s contained within it."""
        return sum((part.duration(bars) for part in self))

    def __repr__(self):
        return "Song(parts={})".format(super(Song, self).__repr__())
