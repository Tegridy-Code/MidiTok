""" Modified version of Octuple with no Program (Track) tokens
To use mainly for tasks handling a single track.

"""

from math import ceil
import json
from pathlib import Path, PurePath
from typing import List, Tuple, Dict, Optional, Union

import numpy as np
from miditoolkit import Instrument, Note, TempoChange

from .midi_tokenizer_base import MIDITokenizer, Vocabulary
from .constants import *


class OctupleMonoEncoding(MIDITokenizer):
    """ Modified version of Octuple with no Program (Track) tokens
    To use mainly for tasks handling a single track.

    :param pitch_range: range of used MIDI pitches
    :param beat_res: beat resolutions, with the form:
            {(beat_x1, beat_x2): beat_res_1, (beat_x2, beat_x3): beat_res_2, ...}
            The keys of the dict are tuples indicating a range of beats, ex 0 to 3 for the first bar
            The values are the resolution, in samples per beat, of the given range, ex 8
    :param nb_velocities: number of velocity bins
    :param additional_tokens: specifies additional tokens (time signature, tempo)
    :param sos_eos_tokens: adds Start Of Sequence (SOS) and End Of Sequence (EOS) tokens to the vocabulary
    :param params: can be a path to the parameter (json encoded) file or a dictionary
    """

    def __init__(self, pitch_range: range = PITCH_RANGE, beat_res: Dict[Tuple[int, int], int] = BEAT_RES,
                 nb_velocities: int = NB_VELOCITIES, additional_tokens: Dict[str, bool] = ADDITIONAL_TOKENS,
                 sos_eos_tokens: bool = False, params=None):
        additional_tokens['Chord'] = False  # Incompatible additional token
        additional_tokens['Rest'] = False
        additional_tokens['Program'] = False
        # used in place of positional encoding
        self.max_bar_embedding = 60  # this attribute might increase during encoding
        super().__init__(pitch_range, beat_res, nb_velocities, additional_tokens, sos_eos_tokens, params)

    def save_params(self, out_dir: Union[str, Path, PurePath]):
        """ Override the parent class method to include additional parameter drum pitch range
        Saves the base parameters of this encoding in a txt file
        Useful to keep track of how a dataset has been tokenized / encoded
        It will also save the name of the class used, i.e. the encoding strategy

        :param out_dir: output directory to save the file
        """
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        with open(PurePath(out_dir, 'config').with_suffix(".txt"), 'w') as outfile:
            json.dump({'pitch_range': (self.pitch_range.start, self.pitch_range.stop),
                       'beat_res': {f'{k1}_{k2}': v for (k1, k2), v in self.beat_res.items()},
                       'nb_velocities': len(self.velocities),
                       'additional_tokens': self.additional_tokens,
                       'encoding': self.__class__.__name__,
                       'max_bar_embedding': self.max_bar_embedding},
                      outfile)

    def track_to_tokens(self, track: Instrument) -> List[List[int]]:
        """ Converts a track (miditoolkit.Instrument object) into a sequence of tokens
        A time step is a list of tokens where:
            (list index: token type)
            0: Pitch
            1: Velocity
            2: Duration
            4: Position
            5: Bar
            (6: Tempo)

        :param track: MIDI track to convert
        :return: sequence of corresponding tokens
        """
        # Make sure the notes are sorted first by their onset (start) times, second by pitch
        # notes.sort(key=lambda x: (x.start, x.pitch))  # done in midi_to_tokens
        ticks_per_sample = self.current_midi_metadata['time_division'] / max(self.beat_res.values())
        ticks_per_bar = self.current_midi_metadata['time_division'] * 4

        # Check bar embedding limit, update if needed
        nb_bars = ceil(max(note.end for note in track.notes) / (self.current_midi_metadata['time_division'] * 4))
        if self.max_bar_embedding < nb_bars:
            self.vocab.add_event(f'Bar_{i}' for i in range(self.max_bar_embedding, nb_bars))
            self.max_bar_embedding = nb_bars

        tokens = []
        current_tick = -1
        current_bar = -1
        current_pos = -1
        current_tempo_idx = 0
        current_tempo = self.current_midi_metadata['tempo_changes'][current_tempo_idx].tempo
        for note in track.notes:
            # Positions and bars
            if note.start != current_tick:
                pos_index = int((note.start % ticks_per_bar) / ticks_per_sample)
                current_tick = note.start
                current_bar = current_tick // ticks_per_bar
                current_pos = pos_index

            # Note attributes
            duration = note.end - note.start
            dur_index = np.argmin(np.abs([ticks - duration for ticks in
                                          self.durations_ticks[self.current_midi_metadata['time_division']]]))
            token_ts = [self.vocab.event_to_token[f'Pitch_{note.pitch}'],
                        self.vocab.event_to_token[f'Velocity_{note.velocity}'],
                        self.vocab.event_to_token[f'Duration_{".".join(map(str, self.durations[dur_index]))}'],
                        self.vocab.event_to_token[f'Position_{current_pos}'],
                        self.vocab.event_to_token[f'Bar_{current_bar}']]

            # (Tempo)
            if self.additional_tokens['Tempo']:
                # If the current tempo is not the last one
                if current_tempo_idx + 1 < len(self.current_midi_metadata['tempo_changes']):
                    # Will loop over incoming tempo changes
                    for tempo_change in self.current_midi_metadata['tempo_changes'][current_tempo_idx + 1:]:
                        # If this tempo change happened before the current moment
                        if tempo_change.time <= note.start:
                            current_tempo = tempo_change.tempo
                            current_tempo_idx += 1  # update tempo value (might not change) and index
                        elif tempo_change.time > note.start:
                            break  # this tempo change is beyond the current time step, we break the loop
                token_ts.append(self.vocab.event_to_token[f'Tempo_{current_tempo}'])

            tokens.append(token_ts)

        return tokens

    def tokens_to_track(self, tokens: List[List[int]], time_division: Optional[int] = TIME_DIVISION,
                        program: Optional[Tuple[int, bool]] = (0, False)) -> Tuple[Instrument, List[TempoChange]]:
        """ Converts a sequence of tokens into a track object
        A time step is a list of tokens where:
            (list index: token type)
            0: Pitch
            1: Velocity
            2: Duration
            4: Position
            5: Bar
            (6: Tempo)

        :param tokens: sequence of tokens to convert
        :param time_division: MIDI time division / resolution, in ticks/beat (of the MIDI to create)
        :param program: the MIDI program of the produced track and if it drum, (default (0, False), piano)
        :return: the miditoolkit instrument object and tempo changes
        """
        assert time_division % max(self.beat_res.values()) == 0, \
            f'Invalid time division, please give one divisible by {max(self.beat_res.values())}'
        events = [self._tokens_to_events(time_step) for time_step in tokens]

        ticks_per_sample = time_division // max(self.beat_res.values())
        name = 'Drums' if program[1] else MIDI_INSTRUMENTS[program[0]]['name']
        instrument = Instrument(program[0], is_drum=program[1], name=name)

        if self.additional_tokens['Tempo']:
            tempo_changes = [TempoChange(int(self._tokens_to_events(tokens[0])[-1].value), 0)]
        else:  # default
            tempo_changes = [TempoChange(TEMPO, 0)]

        for time_step in events:
            # Note attributes
            pitch = int(time_step[0].value)
            vel = int(time_step[1].value)
            duration = self._token_duration_to_ticks(time_step[2].value, time_division)

            # Time and track values
            current_pos = int(time_step[3].value)
            current_bar = int(time_step[4].value)
            current_tick = current_bar * time_division * 4 + current_pos * ticks_per_sample

            # Append the created note
            instrument.notes.append(Note(vel, pitch, current_tick, current_tick + duration))

            # Tempo, adds a TempoChange if necessary
            if self.additional_tokens['Tempo']:
                tempo = int(time_step[-1].value)
                if tempo != tempo_changes[-1].tempo:
                    tempo_changes.append(TempoChange(tempo, current_tick))

        return instrument, tempo_changes

    def _create_vocabulary(self, sos_eos_tokens: bool = False) -> Vocabulary:
        """ Creates the Vocabulary object of the tokenizer.
        See the docstring of the Vocabulary class for more details about how to use it.
        NOTE: token index 0 is often used as a padding index during training

        :param sos_eos_tokens: will include Start Of Sequence (SOS) and End Of Sequence (tokens)
        :return: the vocabulary object
        """
        vocab = Vocabulary({'PAD_None': 0})

        # PITCH
        vocab.add_event(f'Pitch_{i}' for i in self.pitch_range)

        # VELOCITY
        vocab.add_event(f'Velocity_{i}' for i in self.velocities)

        # DURATION
        vocab.add_event(f'Duration_{".".join(map(str, duration))}' for duration in self.durations)

        # POSITION
        nb_positions = max(self.beat_res.values()) * 4  # 4/4 time signature
        vocab.add_event(f'Position_{i}' for i in range(nb_positions))

        # TEMPO
        if self.additional_tokens['Tempo']:
            vocab.add_event(f'Tempo_{i}' for i in self.tempos)

        # SOS & EOS
        if sos_eos_tokens:
            vocab.add_sos_eos_to_vocab()

        # BAR --- MUST BE LAST IN DIC AS THIS MIGHT BE INCREASED
        vocab.add_event('Bar_None')  # new bar token
        vocab.add_event(f'Bar_{i}' for i in range(self.max_bar_embedding))  # bar embeddings (positional encoding)

        return vocab

    def _create_token_types_graph(self) -> Dict[str, List[str]]:
        """ Returns a graph (as a dictionary) of the possible token
        types successions.
        Not relevant for Octuple.

        :return: the token types transitions dictionary
        """
        return {}  # not relevant for this encoding

    def token_types_errors(self, tokens: List[List[int]]) -> float:
        """ Checks if a sequence of tokens is constituted of good token values and
        returns the error ratio (lower is better).
        The token types are always the same in Octuple so this methods only checks
        if their values are correct:
            - a bar token value cannot be < to the current bar (it would go back in time)
            - same for positions
            - a pitch token should not be present if the same pitch is already played at the current position

        :param tokens: sequence of tokens to check
        :return: the error ratio (lower is better)
        """
        err = 0
        current_bar = current_pos = -1
        current_pitches = []

        for token in tokens:
            has_error = False
            bar_value = int(self.vocab.token_to_event[token[4]].split('_')[1])
            pos_value = int(self.vocab.token_to_event[token[3]].split('_')[1])
            pitch_value = int(self.vocab.token_to_event[token[0]].split('_')[1])

            # Bar
            if bar_value < current_bar:
                has_error = True
            elif bar_value > current_bar:
                current_bar = bar_value
                current_pos = -1
                current_pitches = []

            # Position
            if pos_value < current_pos:
                has_error = True
            elif pos_value > current_pos:
                current_pos = pos_value
                current_pitches = []

            # Pitch
            if pitch_value in current_pitches:
                has_error = True
            else:
                current_pitches.append(pitch_value)

            if has_error:
                err += 1

        return err / len(tokens)
