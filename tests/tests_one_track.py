#!/usr/bin/python3 python

""" One track test file
This test method is to be used with MIDI files of one track (like the maestro dataset).
It is mostly useful to measure the performance of encodings where time is based on
time shifts tokens, as these files usually don't contain tracks with very long pauses,
i.e. long duration / time-shift values probably out of range of the tokenizer's vocabulary.

NOTE: encoded tracks has to be compared with the quantized original track.

"""

from sys import stdout
from copy import deepcopy
from pathlib import Path, PurePath
from typing import Union

import miditok
from miditoolkit import MidiFile, Marker

from tests_utils import track_equals, tempo_changes_equals

# Special beat res for test, up to 64 beats so the duration and time-shift values are
# long enough for MIDI-Like and Structured encodings, and with a single beat resolution
BEAT_RES_TEST = {(0, 64): 8}
ADDITIONAL_TOKENS_TEST = {'Chord': True,  # set to false to speed up tests as it takes some time on maestro MIDIs
                          'Rest': True,
                          'Tempo': True,
                          'Program': False,
                          'rest_range': (4, 16),
                          'nb_tempos': 32,
                          'tempo_range': (40, 250)}


def one_track_midi_to_tokens_to_midi(data_path: Union[str, Path, PurePath] = './Maestro_MIDIs',
                                     saving_erroneous_midis: bool = True) -> bool:
    """ Reads a few MIDI files, convert them into token sequences, convert them back to MIDI files.
    The converted back MIDI files should identical to original one, expect with note starting and ending
    times quantized, and maybe a some duplicated notes removed

    :param data_path: root path to the data to test
    :param saving_erroneous_midis: will save MIDIs converted back with errors, to be used to debug
    """
    encodings = ['MIDILikeEncoding', 'StructuredEncoding', 'REMIEncoding', 'CPWordEncoding', 'OctupleEncoding',
                 'OctupleMonoEncoding', 'MuMIDIEncoding']
    files = list(Path(data_path).glob('**/*.mid'))

    for i, file_path in enumerate(files):
        bar_len = 30
        filled_len = int(round(bar_len * i / len(files)))
        percents = round(100.0 * i / len(files), 2)
        bar = '=' * filled_len + '-' * (bar_len - filled_len)
        prog = f'\r{i} / {len(files)} [{bar}] {percents:.1f}% ...Converting MIDIs to tokens: {file_path}'
        stdout.write(prog)
        stdout.flush()

        # Reads the midi
        midi = MidiFile(file_path)
        tracks = [deepcopy(midi.instruments[0])]
        has_errors = False

        for encoding in encodings:
            add_tokens = deepcopy(ADDITIONAL_TOKENS_TEST)
            if encoding == 'MIDILikeEncoding':
                add_tokens['rest_range'] = (max(BEAT_RES_TEST.values()), max(t[1] for t in BEAT_RES_TEST))
            if encoding == 'StructuredEncoding':
                tokenizer = getattr(miditok, encoding)(beat_res=BEAT_RES_TEST)
            else:
                tokenizer = getattr(miditok, encoding)(beat_res=BEAT_RES_TEST, additional_tokens=add_tokens)

            # Convert the track in tokens
            tokens = tokenizer.midi_to_tokens(midi)

            # Checks types and values conformity following the rules
            tokens_types = tokenizer.token_types_errors(tokens[0] if encoding not in ['OctupleEncoding',
                                                                                      'MuMIDIEncoding'] else tokens)
            if tokens_types != 0.:
                print(f'Validation of tokens types / values successions failed with {encoding}: {tokens_types}')

            # Convert back tokens into a track object
            tempo_changes = None
            if encoding == 'OctupleEncoding' or encoding == 'MuMIDIEncoding':
                new_midi = tokenizer.tokens_to_midi(tokens, time_division=midi.ticks_per_beat)
                track = new_midi.instruments[0]
                if encoding == 'OctupleEncoding':
                    tempo_changes = new_midi.tempo_changes
            else:
                track, tempo_changes = tokenizer.tokens_to_track(tokens[0], midi.ticks_per_beat)

            # Checks its good
            errors = track_equals(midi.instruments[0], track)
            if len(errors) > 0:
                has_errors = True
                if errors[0][0] != 'len':
                    for err, note, exp in errors:
                        midi.markers.append(Marker(f'ERR {encoding[:-8]} with note {err} (pitch {note.pitch})',
                                                   note.start))
                print(f'MIDI {i} - {file_path} failed to encode/decode MIDI with '
                      f'{encoding[:-8]} ({len(errors)} errors)')
                # return False
            track.name = f'encoded with {encoding[:-8]}'
            tracks.append(track)

            # Checks tempos
            if tempo_changes is not None and tokenizer.additional_tokens['Tempo']:
                tempo_errors = tempo_changes_equals(midi.tempo_changes, tempo_changes)
                if len(tempo_errors) > 0:
                    has_errors = True
                    print(f'MIDI {i} - {file_path} failed to encode/decode TEMPO changes with '
                          f'{encoding[:-8]} ({len(tempo_errors)} errors)')

        if saving_erroneous_midis and has_errors:
            midi.instruments[0].name = 'original quantized'
            tracks[0].name = 'original not quantized'

            # Updates the MIDI and save it
            midi.instruments += tracks
            midi.dump(PurePath('tests', 'test_results', file_path.name))
    return True


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='MIDI Encoding test')
    parser.add_argument('--data', type=str, default='tests/Maestro_MIDIs',
                        help='directory of MIDI files to use for test')
    args = parser.parse_args()
    one_track_midi_to_tokens_to_midi(args.data)
