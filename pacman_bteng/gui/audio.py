"""Sound effects, synthesised at start-up — there are no audio files.

Same spirit as the renderer, which is all ``pygame.draw`` and no images: every
effect is a few lines of arithmetic turned into 16-bit PCM, so the repository
stays free of binary assets and the sounds are tunable in plain code.

Audio is strictly optional.  A machine with no sound card, a headless run or a
missing mixer all end up as a :class:`SoundBoard` whose :meth:`play` does
nothing; the game never fails to start over a beep.
"""

from __future__ import annotations

import math
import struct
from typing import Dict, Iterable, List, Optional, Tuple

import pygame

SAMPLE_RATE = 22050

#: A note is (frequency Hz, seconds).  ``0`` Hz is a rest.
Note = Tuple[float, float]


def _render(notes: Iterable[Note], *, volume: float = 0.35, square: bool = False,
            slide: float = 0.0) -> bytes:
    """Turn notes into mono 16-bit little-endian PCM.

    *slide* bends each note by that many Hz over its length (the rising
    "whoop" of eating a ghost); *square* gives the harsher chip-tune timbre.
    A short fade at both ends of every note stops the clicks a hard edge makes.
    """
    samples: List[int] = []
    for freq, seconds in notes:
        count = int(SAMPLE_RATE * seconds)
        fade = max(1, min(count // 2, int(SAMPLE_RATE * 0.004)))
        phase = 0.0
        for i in range(count):
            if freq <= 0:
                samples.append(0)
                continue
            phase += 2 * math.pi * (freq + slide * i / max(1, count)) / SAMPLE_RATE
            wave = math.sin(phase)
            if square:
                wave = 1.0 if wave >= 0 else -1.0
            envelope = min(1.0, i / fade, (count - i) / fade)
            samples.append(int(32767 * volume * envelope * wave))
    return struct.pack(f"<{len(samples)}h", *samples)


def _effects() -> Dict[str, bytes]:
    """Every effect, keyed by the :class:`GameEvent` kind that triggers it."""
    return {
        "pellet": _render([(520, 0.045)], volume=0.18),
        "power": _render([(220, 0.07), (330, 0.07), (440, 0.07), (660, 0.12)], square=True),
        "ghost_eaten": _render([(300, 0.22)], slide=900, square=True, volume=0.28),
        "pacman_caught": _render([(520, 0.12), (440, 0.12), (360, 0.12), (280, 0.14),
                                  (200, 0.16), (130, 0.3)], slide=-40),
        "level_start": _render([(392, 0.12), (523, 0.12), (659, 0.12), (784, 0.24)], square=True,
                               volume=0.22),
        "level_clear": _render([(523, 0.1), (659, 0.1), (784, 0.1), (1047, 0.3)]),
        "extra_life": _render([(784, 0.08), (988, 0.08), (1175, 0.08), (1568, 0.2)]),
        "game_over": _render([(392, 0.2), (330, 0.2), (262, 0.2), (196, 0.5)], square=True,
                             volume=0.25),
    }


class SoundBoard:
    """Plays the effect that matches each game event; mute-able at runtime."""

    def __init__(self, enabled: bool = True) -> None:
        self.muted = False
        self._sounds: Dict[str, "pygame.mixer.Sound"] = {}
        self.available = False
        if not enabled:
            return
        try:
            pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=1, buffer=512)
            self._sounds = {k: pygame.mixer.Sound(buffer=v) for k, v in _effects().items()}
            self.available = True
        except Exception:  # noqa: BLE001 - no sound card is not a startup failure
            self._sounds = {}

    def play(self, kind: str) -> None:
        sound: Optional["pygame.mixer.Sound"] = self._sounds.get(kind)
        if sound is not None and not self.muted:
            sound.play()

    def toggle_mute(self) -> bool:
        """Flip mute; returns the new muted state."""
        self.muted = not self.muted
        if self.muted and self.available:
            pygame.mixer.stop()
        return self.muted

    def close(self) -> None:
        if self.available:
            pygame.mixer.quit()
            self.available = False
