"""Bit-exact copy of the C++ random numbers maze.cpp uses (g++ / libstdc++).

maze.cpp seeds std::mt19937 and calls rng(), rng() % n and std::shuffle.
Reproducing those exactly means the same --seed builds the same maze in
Python and in C++, so old logs can be replayed and the two can be tested
against each other.
"""

_MASK32 = 0xFFFFFFFF


class MT19937:
    """std::mt19937 (32-bit Mersenne Twister) seeded like mt19937(seed)."""

    def __init__(self, seed=5489):
        seed &= _MASK32
        mt = [0] * 624
        mt[0] = seed
        for i in range(1, 624):
            mt[i] = (1812433253 * (mt[i - 1] ^ (mt[i - 1] >> 30)) + i) & _MASK32
        self.mt = mt
        self.idx = 624

    def _twist(self):
        mt = self.mt
        for i in range(624):
            y = (mt[i] & 0x80000000) | (mt[(i + 1) % 624] & 0x7FFFFFFF)
            v = mt[(i + 397) % 624] ^ (y >> 1)
            if y & 1:
                v ^= 0x9908B0DF
            mt[i] = v
        self.idx = 0

    def __call__(self):
        """Next 32-bit output, like rng() in C++."""
        if self.idx >= 624:
            self._twist()
        y = self.mt[self.idx]
        self.idx += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        return y & _MASK32


def uniform_int(g, a, b):
    """std::uniform_int_distribution<size_t>{a, b}(g) as libstdc++ does it
    for a 32-bit generator (Lemire's nearly-divisionless method)."""
    urange = b - a
    if urange < _MASK32:
        uerange = urange + 1
        product = g() * uerange
        low = product & _MASK32
        if low < uerange:
            threshold = (-uerange & _MASK32) % uerange
            while low < threshold:
                product = g() * uerange
                low = product & _MASK32
        return a + (product >> 32)
    if urange == _MASK32:
        return a + g()
    # Range wider than 32 bits: libstdc++ "upscaling". Never reached by the
    # maze (it would need billions of rooms), so kept simple and exact.
    uerngrange = _MASK32 + 1
    while True:
        tmp = uerngrange * uniform_int(g, 0, urange // uerngrange)
        ret = tmp + g()
        if ret <= urange and ret >= tmp:
            return a + ret


def shuffle(seq, g):
    """std::shuffle(seq.begin(), seq.end(), g) as libstdc++ does it, in place."""
    n = len(seq)
    if n <= 1:  # libstdc++ draws nothing for 0 or 1 elements
        return
    if _MASK32 // n >= n:
        # Two swap positions from one draw.
        i = 1
        if n % 2 == 0:
            j = uniform_int(g, 0, 1)
            seq[i], seq[j] = seq[j], seq[i]
            i += 1
        while i != n:
            r = i + 1
            x = uniform_int(g, 0, r * (r + 1) - 1)
            p1, p2 = x // (r + 1), x % (r + 1)
            seq[i], seq[p1] = seq[p1], seq[i]
            i += 1
            seq[i], seq[p2] = seq[p2], seq[i]
            i += 1
        return
    for i in range(1, n):
        j = uniform_int(g, 0, i)
        seq[i], seq[j] = seq[j], seq[i]
