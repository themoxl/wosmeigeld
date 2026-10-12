from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import HTTPException, status
from pydantic import BaseModel

from app.core.config import Settings

_AUDIENCE = "authenticated"
# Aktuelle Supabase-CLI-/Supabase-Auth-Standardeinstellung: asymmetrisch
# signierte JWTs (ES256/RS256), verifiziert ueber den JWKS-Endpunkt statt
# eines geteilten HS256-Secrets. SUPABASE_JWT_SECRET bleibt als Fallback fuer
# aeltere/self-hosted Supabase-Instanzen ohne JWT-Signing-Keys erhalten.
_ASYMMETRIC_ALGORITHMS = ["ES256", "RS256"]
# Toleranz gegen Uhrenversatz zwischen Supabase Auth und dieser API. PyJWT
# verwirft ein Token, sobald `iat > now` ist (ImmatureSignatureError) - ohne
# Spielraum genuegt also EINE Sekunde Vorsprung des Auth-Containers, damit ein
# gerade ausgestelltes, voellig gueltiges Token als "invalid_token" mit 401
# abgewiesen wird. Genau das trat lokal auf: die Container-Uhr lief dem Host
# um ein bis zwei Sekunden voraus, worauf jede frische Anmeldung fehlschlug,
# bis der Host aufgeholt hatte. RFC 7519 Abschnitt 4.1.5 sieht diesen
# Spielraum ausdruecklich vor. Der Wert verlaengert auch `exp` um dieselbe
# Spanne - gegenueber der Token-Laufzeit von einer Stunde ist das
# vernachlaessigbar, ein zu enger Wert dagegen sperrt echte Nutzer aus.
_CLOCK_SKEW_LEEWAY_SECONDS = 60


class AuthenticatedUser(BaseModel):
    id: UUID
    email: str | None = None


@lru_cache
def _get_jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(jwks_url, cache_keys=True)


def decode_supabase_jwt(token: str, settings: Settings) -> AuthenticatedUser:
    """Validiert ein Supabase-Auth-JWT lokal.

    Signup/Login laufen client-seitig direkt gegen Supabase Auth
    (Architekturplan Abschnitt 2: "Authentifizierung: Supabase Auth; JWT wird
    durch die BFF-API validiert") - die API prueft hier ausschliesslich die
    Signatur und extrahiert die Nutzer-ID, ohne selbst Tokens auszustellen.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token"
        ) from exc

    try:
        if header.get("alg") in _ASYMMETRIC_ALGORITHMS:
            jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
            signing_key = _get_jwks_client(jwks_url).get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=_ASYMMETRIC_ALGORITHMS,
                audience=_AUDIENCE,
                leeway=_CLOCK_SKEW_LEEWAY_SECONDS,
            )
        else:
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience=_AUDIENCE,
                leeway=_CLOCK_SKEW_LEEWAY_SECONDS,
            )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token"
        ) from exc

    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")

    return AuthenticatedUser(id=UUID(subject), email=payload.get("email"))
