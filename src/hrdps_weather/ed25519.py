"""Ed25519 (RFC 8032) in pure Python: just enough to sign and verify the release checksum files.

Pure Python on purpose: the program must be able to verify an update without extra dependencies, and the
release workflow signs with the very same code. Verifying one signature takes a few milliseconds.
Not constant-time — it is only ever used with a public key (verify) or in CI (sign).
"""
import hashlib

P = 2 ** 255 - 19
Q = 2 ** 252 + 27742317777372353535851937790883648493


def _inv(x):
    return pow(x, P - 2, P)


D = -121665 * _inv(121666) % P
_I = pow(2, (P - 1) // 4, P)


def _recover_x(y, sign):
    x2 = (y * y - 1) * _inv(D * y * y + 1) % P
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * _I % P
    if (x * x - x2) % P:
        raise ValueError("not on the curve")
    if (x & 1) != sign:
        x = P - x
    return x


_GY = 4 * _inv(5) % P
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % P)


def _add(A, B):
    a = (A[1] - A[0]) * (B[1] - B[0]) % P
    b = (A[1] + A[0]) * (B[1] + B[0]) % P
    c = 2 * A[3] * B[3] * D % P
    d = 2 * A[2] * B[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(s, A):
    R = (0, 1, 1, 0)
    while s:
        if s & 1:
            R = _add(R, A)
        A = _add(A, A)
        s >>= 1
    return R


def _compress(A):
    zi = _inv(A[2])
    x, y = A[0] * zi % P, A[1] * zi % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(b):
    y = int.from_bytes(b, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    if y >= P:
        raise ValueError("bad point")
    x = _recover_x(y, sign)
    return (x, y, 1, x * y % P)


def _eq(A, B):
    return (A[0] * B[2] - B[0] * A[2]) % P == 0 and (A[1] * B[2] - B[1] * A[2]) % P == 0


def _h(m):
    return int.from_bytes(hashlib.sha512(m).digest(), "little")


def _expand(seed):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed):
    """32-byte public key for a 32-byte secret seed."""
    return _compress(_mul(_expand(seed)[0], G))


def sign(seed, msg):
    a, prefix = _expand(seed)
    A = _compress(_mul(a, G))
    r = _h(prefix + msg) % Q
    R = _compress(_mul(r, G))
    k = _h(R + A + msg) % Q
    return R + int.to_bytes((r + k * a) % Q, 32, "little")


def verify(pub, msg, sig):
    """True only for a valid signature of `msg` by `pub`; never raises."""
    if len(pub) != 32 or len(sig) != 64:
        return False
    try:
        A, R = _decompress(pub), _decompress(sig[:32])
    except ValueError:
        return False
    S = int.from_bytes(sig[32:], "little")
    if S >= Q:
        return False
    k = _h(sig[:32] + pub + msg) % Q
    return _eq(_mul(S, G), _add(R, _mul(k, A)))
