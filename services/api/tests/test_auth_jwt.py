"""Toleranz der Token-Pruefung gegen Uhrenversatz.

Hintergrund: PyJWT verwirft ein Token, sobald `iat` auch nur eine Sekunde in
der Zukunft liegt (ImmatureSignatureError). Supabase Auth laeuft lokal im
Container, diese API auf dem Host - laeuft die Container-Uhr voraus, wird ein
gerade ausgestelltes, voellig gueltiges Token mit 401 "invalid_token"
abgewiesen. Genau das hat in einem vollen Testlauf zehn Tests in einem
Fenster von rund fuenf Sekunden reihenweise scheitern lassen, waehrend
dieselben Tests einzeln durchliefen.

Die Tests hier pruefen deshalb beide Seiten: kleiner Versatz muss durchgehen,
ein grober Zeitfehler muss weiterhin abgewiesen werden - die Toleranz darf
die Pruefung nicht aushebeln.
"""

import time
from uuid import UUID

import jwt
import pytest
from fastapi import HTTPException

from app.auth.jwt import _CLOCK_SKEW_LEEWAY_SECONDS, decode_supabase_jwt
from app.core.config import get_settings

_USER_ID = "11111111-1111-4111-8111-111111111111"


def _token(*, iat_offset: int = 0, exp_offset: int = 3600) -> str:
    """Baut ein HS256-Token; der Zweig ist derselbe Codepfad wie ES256/RS256,
    nur ohne JWKS-Abruf - die Zeitpruefung liegt hinter der Signaturpruefung."""
    now = int(time.time())
    return jwt.encode(
        {
            "sub": _USER_ID,
            "aud": "authenticated",
            "email": "skew@finanzplaner.test",
            "iat": now + iat_offset,
            "exp": now + exp_offset,
        },
        get_settings().supabase_jwt_secret,
        algorithm="HS256",
    )


def test_token_from_a_clock_that_runs_ahead_is_accepted() -> None:
    """Der eigentliche Regressionsfall: zwei Sekunden Vorsprung."""
    user = decode_supabase_jwt(_token(iat_offset=2), get_settings())

    assert user.id == UUID(_USER_ID)


def test_skew_is_tolerated_up_to_the_documented_limit() -> None:
    user = decode_supabase_jwt(
        _token(iat_offset=_CLOCK_SKEW_LEEWAY_SECONDS), get_settings()
    )

    assert user.id == UUID(_USER_ID)


def test_a_token_issued_far_in_the_future_is_still_rejected() -> None:
    """Die Toleranz ist Spielraum, kein Freibrief."""
    with pytest.raises(HTTPException) as excinfo:
        decode_supabase_jwt(
            _token(iat_offset=_CLOCK_SKEW_LEEWAY_SECONDS + 60), get_settings()
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "invalid_token"


def test_an_expired_token_is_still_rejected() -> None:
    """Der Spielraum verlaengert `exp` um dieselbe Spanne - ein laengst
    abgelaufenes Token darf davon nicht profitieren."""
    with pytest.raises(HTTPException) as excinfo:
        decode_supabase_jwt(
            _token(iat_offset=-7200, exp_offset=-_CLOCK_SKEW_LEEWAY_SECONDS - 60),
            get_settings(),
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "invalid_token"
