#!/usr/bin/python3 python

""" Multitrack test file
This test method will encode every track of a MIDI file.
These file contains tracks with long empty sections with no notes. Hence encodings
in which time is based on time-shift tokens (MIDI-like, Structured) will probably
not be suited for these files.

Structured and MIDI-Like are then not tested here.
You can still manage to make them work and pass the test be using a vocabulary
with large duration / time-shift values, but this is probably not suited for real
case situations.

NOTE: encoded tracks has to be compared with the quantized original track.

"""

from sys import stdout
from copy import deepcopy
from pathlib import Path, PurePath
from typing import Union

import miditok
from miditoolkit import MidiFile

from tests_utils import midis_equals, tempo_changes_equals, reduce_note_durations, adapt_tempo_changes_times

# Special beat res for test, up to 16 beats so the duration and time-shift values are
# long enough for MIDI-Like and Structured encodings, and with a single beat resolution
BEAT_RES_TEST = {(0, 16): 8}
ADDITIONAL_TOKENS_TEST = {'Chord': True,
                          'Rest': True,
                          'Tempo': True,
                          'Program': False,
                          'rest_range': (4, 512),  # very high value to cover every possible rest in the test files
                          'nb_tempos': 32,
                          'tempo_range': (40, 250)}


def multitrack_midi_to_tokens_to_midi(data_path: Union[str, Path, PurePath] = './Maestro_MIDIs',
                                      saving_erroneous_midis: bool = True):
    """ Reads a few MIDI files, convert them into token sequences, convert them back to MIDI files.
    The converted back MIDI files should identical to original one, expect with note starting and ending
    times quantized, and maybe a some duplicated notes removed

    """
    encodings = ['REMIEncoding', 'CPWordEncoding', 'OctupleEncoding', 'OctupleMonoEncoding', 'MuMIDIEncoding']
    files = list(Path(data_path).glob('**/*.mid'))

    for i, file_path in enumerate(files):
        bar_len = 30
        filled_len = int(round(bar_len * i / len(files)))
        percents = round(100.0 * i / len(files), 2)
        bar = '=' * filled_len + '-' * (bar_len - filled_len)
        prog = f'\r{i} / {len(files)} [{bar}] {percents:.1f}% ...Converting MIDIs to tokens: {file_path}'
        stdout.write(prog)
        stdout.flush()

        # Reads the MIDI
        try:
            midi = MidiFile(PurePath(file_path))
        except Exception as _:  # ValueError, OSError, FileNotFoundError, IOError, EOFError, mido.KeySignatureError
            continue
        if midi.ticks_per_beat % max(BEAT_RES_TEST.values()) != 0:
            continue

        for encoding in encodings:
            tokenizer = getattr(miditok, encoding)(beat_res=BEAT_RES_TEST,
                                                   additional_tokens=deepcopy(ADDITIONAL_TOKENS_TEST))

            # MIDI -> Tokens -> MIDI
            new_midi = midi_to_tokens_to_midi(tokenizer, midi)

            # Process the MIDI
            midi_to_compare = deepcopy(midi)  # midi notes / tempos quantized by the line above
            for track in midi_to_compare.instruments:  # reduce the duration of notes to long
                reduce_note_durations(track.notes, max(tu[1] for tu in BEAT_RES_TEST) * midi.ticks_per_beat)
                if track.is_drum:
                    track.program = 0
            # Sort and merge tracks if needed
            # MIDI produced with Octuple contains tracks ordered by program
            if encoding == 'OctupleEncoding' or encoding == 'MuMIDIEncoding':
                miditok.merge_same_program_tracks(midi_to_compare.instruments)  # merge tracks
                midi_to_compare.instruments.sort(key=lambda x: (x.program, x.is_drum))  # sort tracks
                new_midi.instruments.sort(key=lambda x: (x.program, x.is_drum))
            if encoding == 'OctupleEncoding':  # needed
                adapt_tempo_changes_times(midi_to_compare.instruments, midi_to_compare.tempo_changes)

            # Checks notes
            errors = midis_equals(midi_to_compare, new_midi)
            if len(errors) > 0:
                print(f'MIDI {i} - {file_path} failed to encode/decode with '
                      f'{encoding[:-8]} ({sum(len(t) for t in errors)} errors)')
                # return False

            # Checks tempos
            tempo_errors = []
            if tokenizer.additional_tokens['Tempo'] and encoding != 'MuMIDIEncoding':  # MuMIDI doesn't decode tempos
                tempo_errors = tempo_changes_equals(midi_to_compare.tempo_changes, new_midi.tempo_changes)
                if len(tempo_errors) > 0:
                    '''print(f'MIDI {i} - {file_path} failed to encode/decode TEMPO changes with '
                          f'{encoding[:-8]} ({len(tempo_errors)} errors)')'''

            if saving_erroneous_midis and (len(errors) > 0 or len(tempo_errors) > 0):
                new_midi.dump(PurePath('tests', 'test_results', f'{file_path.stem}_{encoding[:-8]}')
                              .with_suffix('.mid'))

    return True


def midi_to_tokens_to_midi(tokenizer: miditok.MIDITokenizer, midi: MidiFile) -> MidiFile:
    """ Converts a MIDI into tokens, and convert them back to MIDI
    Useful to see if the conversion works well in both ways

    :param tokenizer: the tokenizer
    :param midi: MIDI object to convert
    :return: The converted MIDI object
    """
    tokens = tokenizer.midi_to_tokens(midi)
    if len(tokens) == 0:  # no track after notes quantization, this can happen
        return MidiFile()
    inf = miditok.get_midi_programs(midi)  # programs of tracks
    new_midi = tokenizer.tokens_to_midi(tokens, inf, time_division=midi.ticks_per_beat)

    return new_midi


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='MIDI Encoding test')
    parser.add_argument('--data', type=str, default='tests/Multitrack_MIDIs',
                        help='directory of MIDI files to use for test')
    args = parser.parse_args()

    multitrack_midi_to_tokens_to_midi(args.data)
