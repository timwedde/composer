"""
Classes representing a song on a high level.
"""

### System ###
import os
import logging
from math import ceil
from itertools import chain

### Local ###
from mingus.containers import NoteContainer
from mingus.core.progressions import to_chords


def load_song(path):
    """Loads a .sng file as a Song() object"""
    structure = Song()
    chords_per_part = {}
    with open(path, "r") as file:
        main_key = "C"
        for i, line in enumerate(file):
            if i == 0:
                data = line.split(",")
                if 0 < len(data) < 4:
                    structure.name = data[0].strip()
                    if len(data) > 1:
                        structure.author = data[1].strip()
                    if len(data) > 2:
                        main_key = data[2].strip()
                continue
            parts = [l.strip() for l in line.split(",")]
            split_data = [tuple(part.split(":")) for part in parts[1:]]
            chords = []
            for chord in split_data:
                numeral, key, quarters = chord[0], main_key, 4
                if len(chord) > 1:
                    key = chord[1]
                if "/" in numeral:
                    numeral, quarters = numeral.split("/")
                chords.append((numeral, key, int(quarters)))
            song_part = SongPart(parts[0], chords)
            if chords_per_part.get(song_part.name, False) and not song_part:
                song_part = chords_per_part[song_part.name]
            structure.append(song_part)
            chords_per_part[song_part.name] = song_part
    logging.info("Loaded '{}' with structure: {}".format(os.path.basename(path), structure))
    return structure


class SongPart(list):
    """A segment in a song with a name and a chord progression."""

    def __init__(self, name, chords=None):
        if not chords:
            chords = []
        super(SongPart, self).__init__(chords)
        self.name = name

    def duration(self, bars=False, bpm=120):
        """
        Returns the duration in seconds that this part will take.
        If 'bars' is True, returns the length in bars instead.
        """
        beats_per_bar = 4
        _bars = ceil(sum(quarters for chord, key, quarters in self) / beats_per_bar)
        if bars:
            return _bars
        return ceil(((_bars * beats_per_bar) / bpm) * 60)

    def get_midi_chords(self, shift=0):
        """Returns a list of NoteContainer() objects that represent each chord in the progression."""
        chord_list, note_list = [[to_chords(chord, key)[0]] * quarters for chord, key, quarters in self], []
        for chords in chord_list:
            note_list.append([[int(note) + shift for note in NoteContainer(chord)] for chord in chords])
        return list(chain.from_iterable(note_list))

    def __repr__(self):
        return "SongPart(name='{}', chords={})".format(self.name, super(SongPart, self).__repr__())


class Song(list):
    """A container for a list of SongPart()s, which forms a full song."""

    def __init__(self, name=None, author=None, parts=None):
        if not parts:
            parts = []
        self.name = name
        self.author = author
        super(Song, self).__init__(parts)

    def duration(self, bars=False, bpm=120):
        """Returns the summed duration of all SongPart()s contained within it."""
        return sum((part.duration(bars, bpm) for part in self))

    def __repr__(self):
        return "Song(name='{}', author='{}', parts={})".format(self.name, self.author, super(Song, self).__repr__())
