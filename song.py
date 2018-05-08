import logging
from mingus.containers import NoteContainer
from mingus.core.progressions import to_chords
from mingus.core.keys import Key

class CacheItem(object):

    def __init__(self, sequence, response_start_time):
        self.sequence = sequence
        self.response_start_time = response_start_time


class SongPart():

    def __init__(self, name, chords=[]):
        self.name = name
        self.chords = chords

    def get_midi_chords(self, key="C", shift=0):
        output = []
        logging.info(self.chords)
        chords = to_chords(self.chords, key)
        for chord in chords:
            midi_notes = []
            note_container = NoteContainer(chord)
            for note in note_container:
                midi_notes.append(int(note) + shift)
            output.append(midi_notes)
        return output

    def __repr__(self):
        return "SongPart(name=%r, chords=%s)" % (self.name, self.chords)
