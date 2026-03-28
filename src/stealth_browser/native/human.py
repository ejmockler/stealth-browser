"""Human behavior simulation layer for the native engine.

Provides human-like mouse movement (bezier curves), clicking, typing, and
key-press functions on top of the raw ``InputBackend`` protocol.  All
functions are platform-agnostic -- they consume an ``InputBackend`` instance
and delegate low-level OS calls to it.

This is the "how it looks human" layer; ``input.py`` is the "how we talk to
the OS" layer.
"""

from __future__ import annotations

import math
import sys
import time
from random import gauss, randint, uniform

from stealth_browser.native.input import InputBackend, get_keymap

# Resolved once at import time for the current platform.
_KEYMAP = get_keymap()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_keycode(key_name: str) -> int:
    """Map a logical key name to the platform-specific integer keycode.

    Uses the keymap from ``input.get_keymap()`` (resolved at import time).
    Falls back to the ASCII ordinal if the name is a single character.
    """
    code = _KEYMAP.get(key_name)
    if code is not None:
        return code

    # Single printable character -- use its ordinal as a last resort.
    if len(key_name) == 1:
        return ord(key_name)

    raise ValueError(
        f"Cannot resolve keycode for {key_name!r} on platform {sys.platform!r}"
    )


def _min_jerk(tau: float) -> float:
    """Minimum jerk trajectory profile: maps normalized time [0,1] to
    normalized displacement [0,1].

    Minimizes ∫(d³x/dt³)²dt — the smoothest possible voluntary movement.
    This is the trajectory the human motor system naturally produces.
    """
    # 5th-order polynomial: 10τ³ - 15τ⁴ + 6τ⁵
    t3 = tau * tau * tau
    return 10.0 * t3 - 15.0 * t3 * tau + 6.0 * t3 * tau * tau


def _fitts_duration(distance: float, target_width: float = 20.0) -> float:
    """Fitts' law movement duration in seconds.

    T = a + b · log₂(1 + D/W)

    Constants calibrated from empirical trackpad/mouse data.
    """
    a = 0.05  # base reaction time
    b = 0.15  # movement time coefficient
    if distance < 1:
        return a
    return a + b * math.log2(1.0 + distance / target_width)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def move_to(
    backend: InputBackend,
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
) -> None:
    """Move mouse using a minimum-jerk trajectory with signal-dependent noise.

    Models real wrist/finger biomechanics:
    - Nearly straight path with slight curvature from wrist pivot
    - Smooth S-shaped velocity profile (minimum jerk)
    - Duration follows Fitts' law
    - Noise proportional to velocity (signal-dependent, not random jitter)
    - Optional corrective sub-movement near target
    """
    dx = float(to_x - from_x)
    dy = float(to_y - from_y)
    distance = math.hypot(dx, dy)

    if distance < 1:
        backend.mouse_move(to_x, to_y)
        return

    # --- Duration from Fitts' law ---
    duration = _fitts_duration(distance)
    duration *= uniform(0.9, 1.1)  # natural variance

    # --- Slight curvature from wrist pivot ---
    # Perpendicular offset: small, proportional to distance,
    # simulating the arc a wrist makes when pivoting.
    perp_x = -dy / distance
    perp_y = dx / distance
    # Curvature is very subtle: 1-4% of distance (wrist pivot barely curves)
    curvature = uniform(0.01, 0.04) * distance
    if uniform(0, 1) < 0.5:
        curvature = -curvature

    # --- Generate path points ---
    # ~60 Hz update rate (matching typical display refresh)
    n_points = max(8, int(duration / 0.016))
    noise_scale = 0.001  # signal-dependent noise: ~0.1% of velocity (sub-pixel tremor)

    prev_x, prev_y = float(from_x), float(from_y)

    for i in range(1, n_points + 1):
        tau = i / n_points
        s = _min_jerk(tau)

        # Base position: straight line interpolation
        bx = from_x + dx * s
        by = from_y + dy * s

        # Add wrist-pivot curvature: peaks at midpoint, zero at endpoints
        # Shaped by sin(π·τ) — smooth bulge in the middle
        curve_amount = math.sin(math.pi * tau) * curvature
        bx += perp_x * curve_amount
        by += perp_y * curve_amount

        # Signal-dependent noise: proportional to instantaneous velocity.
        # The derivative of _min_jerk at tau gives the velocity profile.
        # v(τ) = 30τ² - 60τ³ + 30τ⁴
        v_tau = 30 * tau * tau - 60 * tau ** 3 + 30 * tau ** 4
        noise_amplitude = noise_scale * distance * v_tau
        bx += gauss(0, max(0.3, noise_amplitude))
        by += gauss(0, max(0.3, noise_amplitude))

        ix, iy = int(round(bx)), int(round(by))

        # Only emit if position actually changed (avoids redundant events)
        if ix != int(round(prev_x)) or iy != int(round(prev_y)):
            backend.mouse_move(ix, iy)

        prev_x, prev_y = bx, by
        time.sleep(duration / n_points)

    # --- Corrective sub-movement ---
    # Humans overshoot slightly ~60% of the time, then correct.
    if uniform(0, 1) < 0.6 and distance > 30:
        overshoot = uniform(2, 5)
        ox = to_x + int(dx / distance * overshoot)
        oy = to_y + int(dy / distance * overshoot)
        backend.mouse_move(ox, oy)
        time.sleep(uniform(0.04, 0.09))
        backend.mouse_move(to_x, to_y)
    else:
        backend.mouse_move(to_x, to_y)


def click_at(
    backend: InputBackend,
    x: int,
    y: int,
    current_x: int = 0,
    current_y: int = 0,
) -> None:
    """Move to (*x*, *y*) with a bezier curve, then click with human timing.

    *current_x* / *current_y* is where the cursor currently sits (default
    origin).  A small random jitter is applied to the final click position to
    avoid pixel-perfect targeting.
    """
    # 1. Bezier move
    move_to(backend, current_x, current_y, x, y)

    # 2. Small jitter
    jitter_x = randint(-3, 3)
    jitter_y = randint(-3, 3)
    final_x = x + jitter_x
    final_y = y + jitter_y

    # 3. Human reaction delay
    time.sleep(uniform(0.02, 0.08))

    # 4. Mouse down
    backend.mouse_down(final_x, final_y)

    # 5. Human hold duration
    time.sleep(uniform(0.04, 0.12))

    # 6. Mouse up
    backend.mouse_up(final_x, final_y)


def type_text(
    backend: InputBackend,
    text: str,
    wpm: int = 65,
) -> None:
    """Type *text* character-by-character with human timing variance.

    *wpm* is the target words-per-minute (standard WPM assumes 5 characters
    per word).  Occasional longer pauses simulate "thinking" between bursts.
    """
    if not text:
        return

    base_delay = 60.0 / (wpm * 5)  # seconds per character
    min_delay = base_delay * 0.3
    max_delay = base_delay * 3.0

    # Next "thinking pause" fires after this many characters.
    next_pause_at = randint(5, 15)
    chars_since_pause = 0

    for char in text:
        # Per-character delay with gaussian jitter.
        delay = gauss(base_delay, base_delay * 0.3)
        delay = max(min_delay, min(max_delay, delay))

        backend.type_char(char)
        time.sleep(delay)

        chars_since_pause += 1
        if chars_since_pause >= next_pause_at:
            time.sleep(uniform(0.1, 0.4))  # thinking pause
            chars_since_pause = 0
            next_pause_at = randint(5, 15)


def press_key(backend: InputBackend, key: str) -> None:
    """Press and release a single key with human timing."""
    keycode = _resolve_keycode(key)
    backend.key_down(keycode)
    time.sleep(uniform(0.03, 0.08))
    backend.key_up(keycode)


def select_all(backend: InputBackend) -> None:
    """Select all text in the focused field.

    Uses Cmd+A on macOS and Ctrl+A everywhere else.
    """
    if sys.platform == "darwin":
        modifier = _resolve_keycode("Meta")
    else:
        modifier = _resolve_keycode("Control")

    a_code = _resolve_keycode("a")

    backend.key_down(modifier)
    backend.key_down(a_code)
    backend.key_up(a_code)
    backend.key_up(modifier)
